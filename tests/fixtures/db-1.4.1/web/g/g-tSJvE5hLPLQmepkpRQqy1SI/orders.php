<?php
// Keeps one print order sent from this gallery, for Lumen Aureum to collect on its next check. The client's browser
// sealed it to the studio's public key with the gallery key mixed in (the shared sealed format, LAS1, purpose
// print-order, mode 03: see sealed.php and docs/sealed-format.md), so only the app can open it and only someone
// holding the gallery link could have made it. This host never holds a key that opens it.
// In order: POST only; the gallery's window (as gate.php: a closed gallery takes no order); the key proof (as
// gate.php, sharing its wrong-proof count); a gallery moved to another host refuses (409, with the studio's email);
// exactly the form fields d (the envelope, base64url) and w (8 bytes of proof of work, hex); the envelope's shape,
// bound to this gallery (this folder's name) and the studio key's ID (offer-kid.php, written beside the offer when
// the photographer turns prints on; without it this gallery takes no order); a proof of work of 17 bits; then the
// limits: 5 orders an hour and 10 waiting from one sender (not behind a proxy: shared=1), 100 files and 6.5 MB waiting
// in all. Each order is kept as orders/<first 32 hex of its SHA-256>.las, so the same envelope sent again is kept once.
// There is no read path: no order is ever listed or served from here (orders/ holds an empty index.html).
header('X-Host-Scripts: 1.4.0');
header('Cache-Control: no-store');
header('X-Content-Type-Options: nosniff');
if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST') { http_response_code(405); exit; }
// A state file: its PHP form, after the exit line, or (a folder from before this layout) its plain one
function st($n) {
    $m = ['limits.txt' => 'limits.php', 'seen.txt' => 'seen-at.php', 'once.txt' => 'once.php', 'done.txt' => 'done.php'];
    $raw = @file_get_contents(__DIR__ . '/' . $m[$n]);
    if ($raw !== false) { return strncmp($raw, "<?php exit; ?>\n", 15) === 0 ? (string)substr($raw, 15) : $raw; }
    return @file_get_contents(__DIR__ . '/' . $n);
}
$t = st('limits.txt');
$lim = $t === false ? false : @parse_ini_string($t);
if ($lim === false || !isset($lim['days'], $lim['unopened_until'])) { http_response_code(410); exit; }
function day_end($lim, $ts, $day) {             // as in gate.php: the exact instant, else the date's end in UTC (before 1.3)
    if (isset($lim[$ts]) && ctype_digit((string)$lim[$ts])) { return (int)$lim[$ts]; }
    return isset($lim[$day]) ? strtotime($lim[$day] . 'T23:59:59Z') : false;
}
$seen = st('seen.txt');
$end = $seen ? strtotime(trim($seen)) + 86400 * (int)$lim['days'] : day_end($lim, 'unopened_ts', 'unopened_until');
if (isset($lim['until']) || isset($lim['until_ts'])) { $end = day_end($lim, 'until_ts', 'until'); }
$done = st('done.txt');
if ($done !== false && ctype_digit(trim($done)) && (int)trim($done) < $end) { $end = (int)trim($done); }
if ($end === false || time() > $end) { http_response_code(410); exit; }
$raw = @file_get_contents(__DIR__ . '/seen-key.php');
$want = $raw === false ? false : trim(substr($raw, strpos($raw, "\n") + 1));
function sender($want) {                        // an IPv4 address, or an IPv6 /56, as a hash keyed by this gallery's proof
    $ip = (string)($_SERVER['REMOTE_ADDR'] ?? '');
    $bin = @inet_pton($ip);
    if ($bin === false) { $net = $ip; }
    elseif (strlen($bin) === 16 && substr($bin, 0, 12) === str_repeat("\0", 10) . "\xff\xff") { $net = substr($bin, 12); }
    elseif (strlen($bin) === 16) { $net = substr($bin, 0, 7); }
    else { $net = $bin; }
    return 'a' . substr(hash_hmac('sha256', $net, (string)$want), 0, 16);
}
// The key proof, as gate.php (the same in gate.php, picks.php and orders.php). A right proof is never refused because of
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
$proof = trim((string)($_SERVER['HTTP_X_PROOF'] ?? ''));
if (!preg_match('/^[0-9a-f]{32}$/', $proof) || $want === false || !proof_ok($want, $proof, $lim)) { http_response_code(403); exit; }
if (!empty($lim['moved'])) {
    http_response_code(409);
    header('Content-Type: application/json');
    $c = isset($lim['contact']) ? (string)$lim['contact'] : '';
    $ok = $c !== '' && strlen($c) <= 254 && filter_var($c, FILTER_VALIDATE_EMAIL) && !preg_match('/["\'<>\\\\]/', $c);
    echo json_encode($ok ? ['moved' => 1, 'contact' => $c] : ['moved' => 1]);
    exit;
}
// Being updated (limits.php updating=1: the 1.4 move to a link ID is carrying what was sent here over to the gallery's
// new folder, and the page that forwards there is about to go up). Nothing sent here now would be read: refused, to be
// sent again in a minute (from the new folder, once this one forwards there).
if (!empty($lim['updating'])) {
    http_response_code(503);
    header('Retry-After: 60');
    header('Content-Type: application/json');
    echo json_encode(['updating' => 1]);
    exit;
}
$ct = strtolower(trim(explode(';', (string)($_SERVER['CONTENT_TYPE'] ?? ''))[0]));
if ($ct !== 'application/x-www-form-urlencoded') { http_response_code(400); exit; }
if (count($_GET) !== 0 || count($_POST) !== 2 || !isset($_POST['d'], $_POST['w']) || !is_string($_POST['d']) || !is_string($_POST['w'])
    || !preg_match('/^[0-9a-f]{16}$/', $_POST['w'])) { http_response_code(400); exit; }
require __DIR__ . '/sealed.php';
list($body, $err) = sealed_decode($_POST['d'], 'print-order');
if ($err === '') { $err = sealed_host_check($body, 'print-order', basename(__DIR__), null); }
if ($err !== '') { http_response_code(sealed_status($err)); exit; }
// prints are on for this gallery only while the offer's key ID is here
$kraw = @file_get_contents(__DIR__ . '/offer-kid.php');
$KID = $kraw === false ? '' : trim(substr($kraw, (int)strpos($kraw, "\n") + 1));
if (!preg_match('/^[0-9a-f]{16}$/', $KID)) { http_response_code(410); exit; }
$err = sealed_check($body, 'print-order', basename(__DIR__), $KID);
if ($err !== '') { http_response_code(400); exit; }
// the proof of work: two hashes to check, however long it took to find
$h = hash('sha256', hash('sha256', $body, true) . hex2bin($_POST['w']), true);
$need = 17;
for ($i = 0; $need >= 8; $i++, $need -= 8) { if ($h[$i] !== "\0") { http_response_code(403); exit; } }
if ($need > 0 && (ord($h[$i]) >> (8 - $need)) !== 0) { http_response_code(403); exit; }
$dir = __DIR__ . '/orders';
if (!is_dir($dir) && !@mkdir($dir, 0775) && !is_dir($dir)) { http_response_code(500); exit; }
// an empty index.html, so a web server that lists folders shows nothing here
if (!is_file($dir . '/index.html')) { @file_put_contents($dir . '/index.html', ''); }
$name = substr(hash('sha256', $body), 0, 32) . '.las';
if (is_file($dir . '/' . $name)) { http_response_code(204); exit; }      // the same order sent again: kept once
// the limits, counted under one lock in orders/tries.php (it starts with a PHP exit line, so fetching it shows
// nothing; each sender is a keyed hash, never the address)
$fh = @fopen($dir . '/tries.php', 'c+');
if ($fh === false || !flock($fh, LOCK_EX)) { http_response_code(503); exit; }
$out = function ($code) use ($fh) { flock($fh, LOCK_UN); fclose($fh); http_response_code($code); exit; };
$now = time();
$who = sender($want);
$raw = (string)stream_get_contents($fh);
$all = json_decode((string)substr($raw, (int)strpos($raw, "\n") + 1), true);
if (!is_array($all)) { $all = []; }
$waiting = [];
$n = 0; $total = 0;
foreach (glob($dir . '/*.las') ?: [] as $f) { $waiting[basename($f)] = true; $n++; $total += (int)@filesize($f); }
foreach ($all as $k => $v) {
    $t = isset($v['t']) && is_array($v['t']) ? array_values(array_filter($v['t'], function ($x) use ($now) { return is_int($x) && $x > $now - 3600; })) : [];
    $w = isset($v['w']) && is_array($v['w']) ? array_values(array_filter($v['w'], function ($x) use ($waiting) { return is_string($x) && isset($waiting[$x]); })) : [];
    if (($t || $w) && is_string($k)) { $all[$k] = ['t' => $t, 'w' => $w]; } else { unset($all[$k]); }
}
$mine = $all[$who] ?? ['t' => [], 'w' => []];
// Behind a proxy or CDN (limits.php shared=1, from the web host check) every client arrives from the same few
// addresses, so the limits per sender would be limits for everyone: there only the gallery's own limits below apply.
// Over the hourly limit, Retry-After says when the oldest of the five is an hour old, so the page can say when to try
// again; 10 waiting has no such time (they wait for the app to collect them).
if (empty($lim['shared']) && count($mine['t']) >= 5) { header('Retry-After: ' . max(1, min($mine['t']) + 3600 - $now)); $out(429); }
if (empty($lim['shared']) && count($mine['w']) >= 10) { $out(429); }
if ($n >= 100 || $total + strlen($body) > 6815744) { $out(507); }
$tmp = $dir . '/' . $name . '.' . bin2hex(random_bytes(6)) . '.tmp';
if (@file_put_contents($tmp, $body) !== strlen($body) || !@rename($tmp, $dir . '/' . $name)) { @unlink($tmp); $out(500); }
$mine['t'][] = $now;
$mine['w'][] = $name;
$all[$who] = $mine;
ftruncate($fh, 0); rewind($fh); fwrite($fh, "<?php exit; ?>\n" . json_encode($all) . "\n"); fflush($fh);
$out(204);
