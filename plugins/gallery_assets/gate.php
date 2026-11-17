<?php
// Serves the gallery's encrypted files only while the gallery is still within its window, so it expires on time even
// if Lumen Aureum is locked or switched off. The window: limits.php (days after the first visit, or a date if the
// gallery is never opened), written by Lumen Aureum at upload and on an extension. The files are ciphertext either way.
// The manifest, the print offer and the client's picks and draft are kept under names made from the key proof (see
// hname), so on any web server, whether or not it reads .htaccess, nobody without the key can fetch them by name.
// The gallery's state (limits.php, seen-at.php, once.php, done.php) begins with a PHP exit line, so fetching it shows
// nothing. Nothing under orders/ is ever served.
header('X-Host-Scripts: 1.4.0');
header('Cache-Control: no-store');
header('X-Content-Type-Options: nosniff');
// A state file: its PHP form, after the exit line, or (a folder from before this layout) its plain one
function st($n) {
    $m = ['limits.txt' => 'limits.php', 'seen.txt' => 'seen-at.php', 'once.txt' => 'once.php', 'done.txt' => 'done.php'];
    $raw = @file_get_contents(__DIR__ . '/' . $m[$n]);
    if ($raw !== false) { return strncmp($raw, "<?php exit; ?>\n", 15) === 0 ? (string)substr($raw, 15) : $raw; }
    return @file_get_contents(__DIR__ . '/' . $n);
}
// The web host's name of a file sealed under the gallery key (manifest.bin, offer.bin, picks.las, draft.las): the
// first 24 hex of SHA-256("lumen-aureum file name" 0 name 0 proof), with its extension. Only a visitor who sent the
// right proof learns it; the app and the page work it out the same way.
function hname($plain, $proof) {
    return substr(hash('sha256', "lumen-aureum file name\0" . $plain . "\0" . $proof), 0, 24) . substr($plain, -4);
}
$name = basename((string)($_GET['f'] ?? ''));
if (!preg_match('/^([0-9a-f]{24}|manifest|offer)\.bin$|^(draft|picks)\.las$/', $name)) { http_response_code(404); exit; }
$t = st('limits.txt');
$lim = $t === false ? false : @parse_ini_string($t);
// limits.php is uploaded with the gallery and rewritten on every check: missing or unreadable means closed, never open
if ($lim === false || !isset($lim['days'], $lim['unopened_until'])) { http_response_code(410); exit; }
// Closed: the gallery has gone dark. The answer names the photographer's email (limits.php contact, from Settings), the
// one way the page can still offer to reach them, since everything else about the studio is inside the sealed files.
function dark($lim) {
    http_response_code(410);
    $c = isset($lim['contact']) ? (string)$lim['contact'] : '';
    if ($c !== '' && strlen($c) <= 254 && filter_var($c, FILTER_VALIDATE_EMAIL) && !preg_match('/["\'<>\\\\]/', $c)) {
        header('Content-Type: application/json');
        echo json_encode(['contact' => $c]);
    }
    exit;
}
// A fixed last day closes at the exact instant Lumen Aureum wrote (limits.php unopened_ts, until_ts: the end of that day
// in the photographer's time zone, as a Unix time). Galleries written before 1.3 have only the date: the end of it in UTC.
function day_end($lim, $ts, $day) {
    if (isset($lim[$ts]) && ctype_digit((string)$lim[$ts])) { return (int)$lim[$ts]; }
    return isset($lim[$day]) ? strtotime($lim[$day] . 'T23:59:59Z') : false;
}
{
    $seen = st('seen.txt');
    $end = $seen ? strtotime(trim($seen)) + 86400 * (int)$lim['days'] : day_end($lim, 'unopened_ts', 'unopened_until');
    // put back online after it went dark: a fixed last day (limits.php until), whatever the visits
    if (isset($lim['until']) || isset($lim['until_ts'])) { $end = day_end($lim, 'until_ts', 'until'); }
    // a one-time download (limits.php once: the Download all pieces): once every piece has been sent, it closes an
    // hour later (time to finish saving), whatever its last day
    $done = st('done.txt');
    if ($done !== false && ctype_digit(trim($done)) && (int)trim($done) < $end) { $end = (int)trim($done); }
    if ($end === false || time() > $end) { dark($lim); }   // an unreadable date closes it too
    // In its last days (limits.php warn_days) the page says the gallery is about to sunset, and when it closes
    // (sent with the file itself, so only a visitor the gate serves learns the date)
    $warn = isset($lim['warn_days']) ? (int)$lim['warn_days'] : 0;
    $sunset = $warn > 0 && $end - time() <= 86400 * $warn ? gmdate('Y-m-d\TH:i:s\Z', $end) : '';
}
// The key proof (proof_ok, the same in gate.php, picks.php and orders.php). A right proof is never refused because of
// what anyone else sent. A wrong one is refused (403). In a gallery with a codeword (limits.php codeword=1, or a
// gallery written before that line), wrong proofs are counted for the gallery as a whole in tries.php (it starts
// with a PHP exit line, so fetching it shows nothing, and keeps only times): after 10 in an hour, every proof must
// come with a proof of work (X-Work: <hour>-<16 hex>, so SHA-256(SHA-256("lumen-aureum gate work" 0 proof 0 hour) .
// work) starts with 16 zero bits, one more for each doubling of the wrong proofs, at most 20). Without it the answer is
// 429 with X-Work: <bits> <hour>, which the page works out (a second or so, once an hour) and sends again. So each
// guess at a codeword costs work, and nobody, on a shared address or behind a proxy, is ever locked out. Nothing ever
// waits on the host.
function need_work($lim) {
    if (isset($lim['codeword']) && (string)$lim['codeword'] === '0') { return 0; }
    $fh = @fopen(__DIR__ . '/tries.php', 'r');
    if ($fh === false) { return 0; }
    $raw = (string)stream_get_contents($fh);
    fclose($fh);
    $all = json_decode((string)substr($raw, (int)strpos($raw, "\n") + 1), true);
    $now = time();
    $n = 0;
    foreach ((is_array($all) && isset($all['t']) && is_array($all['t'])) ? $all['t'] : [] as $t) { if (is_int($t) && $t > $now - 3600) { $n++; } }
    if ($n < 10) { return 0; }
    return min(20, 16 + (int)floor(log($n / 10, 2)));
}
function work_ok($proof, $bits) {
    $w = (string)($_SERVER['HTTP_X_WORK'] ?? '');
    $hour = intdiv(time(), 3600);
    if (!preg_match('/^(\d{1,12})-([0-9a-f]{16})$/', $w, $m) || ((int)$m[1] !== $hour && (int)$m[1] !== $hour - 1)) { return false; }
    $h = hash('sha256', hash('sha256', "lumen-aureum gate work\0" . $proof . "\0" . $m[1], true) . hex2bin($m[2]), true);
    $need = $bits;
    for ($i = 0; $need >= 8; $i++, $need -= 8) { if ($h[$i] !== "\0") { return false; } }
    return $need === 0 || (ord($h[$i]) >> (8 - $need)) === 0;
}
function proof_ok($want, $proof, $lim) {
    $bits = need_work($lim);
    if ($bits > 0 && !work_ok($proof, $bits)) {
        http_response_code(429);
        header('X-Work: ' . $bits . ' ' . intdiv(time(), 3600));
        header('Access-Control-Expose-Headers: X-Work');
        exit;
    }
    $ok = hash_equals($want, hash('sha256', $proof));
    if (!$ok && !(isset($lim['codeword']) && (string)$lim['codeword'] === '0')) {
        $fh = @fopen(__DIR__ . '/tries.php', 'c+');
        if ($fh !== false && flock($fh, LOCK_EX)) {
            $raw = (string)stream_get_contents($fh);
            $all = json_decode((string)substr($raw, (int)strpos($raw, "\n") + 1), true);
            $now = time();
            $t = (is_array($all) && isset($all['t']) && is_array($all['t'])) ? $all['t'] : [];
            $t = array_values(array_filter($t, function ($x) use ($now) { return is_int($x) && $x > $now - 3600; }));
            $t[] = $now;
            $t = array_slice($t, -4096);
            ftruncate($fh, 0); rewind($fh); fwrite($fh, "<?php exit; ?>\n" . json_encode(['t' => $t]) . "\n"); fflush($fh);
            flock($fh, LOCK_UN);
        }
        if ($fh !== false) { fclose($fh); }
    }
    return $ok;
}
$proved = false;
$proof = '';
if (!empty($lim['proof']) || $name === 'draft.las' || $name === 'picks.las') {
    $proof = trim((string)($_SERVER['HTTP_X_PROOF'] ?? ''));
    if (!preg_match('/^[0-9a-f]{32}$/', $proof)) { http_response_code(403); exit; }
    $raw = @file_get_contents(__DIR__ . '/seen-key.php');
    $want = $raw === false ? false : trim(substr($raw, strpos($raw, "\n") + 1));
    if ($want === false || !proof_ok($want, $proof, $lim)) { http_response_code(403); exit; }
    $proved = true;
}
// The file asked for: a sealed file by its web host name (with the proof), else by the plain name it had before
$p = __DIR__ . '/' . $name;
if (!preg_match('/^[0-9a-f]{24}\.bin$/', $name)) {
    $p = $proved && is_file(__DIR__ . '/' . hname($name, $proof)) ? __DIR__ . '/' . hname($name, $proof) : __DIR__ . '/' . $name;
}
if (!is_file($p)) { http_response_code(404); exit; }
if ($sunset !== '') { header('X-Sunset: ' . $sunset); }
// With the manifest, for a visitor who proved the key: whether the client's draft and sent picks are here, so the page
// only asks for the ones that are (no request answered "not found" on every visit)
if ($name === 'manifest.bin' && $proved) {
    $have = array_values(array_filter(['draft', 'picks'], function ($n) use ($proof) {
        return is_file(__DIR__ . '/' . hname($n . '.las', $proof)) || is_file(__DIR__ . '/' . $n . '.las'); }));
    header('X-Saved: ' . ($have ? implode(',', $have) : 'none'));
}
$once = isset($lim['once']) ? array_filter(explode(',', (string)$lim['once']), function ($n) { return preg_match('/^[0-9a-f]{24}\.bin$/', $n); }) : [];
if ($once) {
    if ($name === 'manifest.bin') {               // the page says: one download (and, once it was made, that it was)
        header('X-Once: ' . gmdate('Y-m-d\TH:i:s\Z', $end));
        if ($done !== false && ctype_digit(trim($done))) { header('X-Once-Done: 1'); }
    }
    if (in_array($name, $once, true)) {         // a piece of Download all: noted; the last one starts the hour
        $f = fopen(__DIR__ . '/once.php', 'c+');
        if ($f && flock($f, LOCK_EX)) {
            $raw = (string)stream_get_contents($f);
            $raw = strncmp($raw, "<?php exit; ?>\n", 15) === 0 ? (string)substr($raw, 15) : '';
            $old = @file_get_contents(__DIR__ . '/once.txt');     // the list as a gate before this layout kept it
            $got = array_values(array_unique(array_filter(array_merge(explode("\n", $raw), $old === false ? [] : explode("\n", $old)))));
            if (!in_array($name, $got, true) || $old !== false) {
                if (!in_array($name, $got, true)) { $got[] = $name; }
                ftruncate($f, 0); rewind($f); fwrite($f, "<?php exit; ?>\n" . implode("\n", $got) . "\n"); fflush($f);
                if ($old !== false) { @unlink(__DIR__ . '/once.txt'); }
            }
            if (!array_diff($once, $got) && st('done.txt') === false) {
                @file_put_contents(__DIR__ . '/done.php', "<?php exit; ?>\n" . (string)(time() + 3600));
            }
            flock($f, LOCK_UN);
        }
        if ($f) { fclose($f); }
    }
}
header('Content-Type: application/octet-stream');
header('Content-Length: ' . filesize($p));
readfile($p);
