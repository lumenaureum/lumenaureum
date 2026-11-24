<?php
// Saves the client's picks, sealed in their browser, as one file beside the gallery (picks.las, kept under the name
// made from the key proof, see gate.php hname; the latest submission wins). With ?kind=draft it saves the picks still
// being made instead (draft.las, the same checks), so the client can
// carry on from another device and the app can show they are still picking. Lumen Aureum fetches it on its regular
// gallery check and opens it there; this host holds no key that opens it.
// It accepts only a sealed envelope in the shared sealed format (LAS1, see sealed.php and docs/sealed-format.md): the
// purpose picks or draft, bound to this gallery's ID (this folder's name), 256 KB at most, and not readable text. Only a
// visitor holding the key can write it: the X-Proof header is sha256 of the key (32 hex), checked against its SHA-256 in
// seen-key.php, the same proof seen.php checks.
header('X-Host-Scripts: 1.5.0');
header('Cache-Control: no-store');
header('X-Content-Type-Options: nosniff');
if ($_SERVER['REQUEST_METHOD'] !== 'POST') { http_response_code(405); exit; }
$kind = (string)($_GET['kind'] ?? '');
if ($kind !== '' && $kind !== 'draft') { http_response_code(400); exit; }
$purpose = $kind === 'draft' ? 'draft' : 'picks';
$name = $purpose . '.las';
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
// The same window as gate.php: once the gallery has closed, nothing more is written (missing or unreadable limits.php
// means closed, never open; so does an unreadable date).
$t = st('limits.txt');
$lim = $t === false ? false : @parse_ini_string($t);
if ($lim === false || !isset($lim['days'], $lim['unopened_until'])) { http_response_code(410); exit; }
function day_end($lim, $ts, $day) {             // as in gate.php: the exact instant, else the date's end in UTC (before 1.3)
    if (isset($lim[$ts]) && ctype_digit((string)$lim[$ts])) { return (int)$lim[$ts]; }
    return isset($lim[$day]) ? strtotime($lim[$day] . 'T23:59:59Z') : false;
}
$seen = st('seen.txt');
$end = $seen ? strtotime(trim($seen)) + 86400 * (int)$lim['days'] : day_end($lim, 'unopened_ts', 'unopened_until');
if (isset($lim['until']) || isset($lim['until_ts'])) { $end = day_end($lim, 'until_ts', 'until'); }   // put back online: a fixed last day
if ($end === false || time() > $end) { http_response_code(410); exit; }
$raw = @file_get_contents(__DIR__ . '/seen-key.php');
$want = $raw === false ? false : trim(substr($raw, strpos($raw, "\n") + 1));
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
// Moved to another web host (limits.php moved=1, written by Move galleries): this copy is still there to look at
// until the photographer takes it down, but picks sent here would never be read. Refused, with the photographer's email.
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
// The page sends the envelope as one form field (d=, base64url without padding), which web firewalls accept.
if (!isset($_POST['d']) || !is_string($_POST['d'])) { http_response_code(400); exit; }
require __DIR__ . '/sealed.php';
list($body, $err) = sealed_decode($_POST['d'], $purpose);
if ($err === '') { $err = sealed_host_check($body, $purpose, basename(__DIR__), null); }
if ($err !== '') { http_response_code(sealed_status($err)); exit; }
$len = strlen($body);
$name = hname($name, $proof);                    // its web host name (gate.php hname): only the key finds it
$tmp = __DIR__ . '/' . $name . '.' . bin2hex(random_bytes(6)) . '.tmp';
if (@file_put_contents($tmp, $body, LOCK_EX) !== $len || !@rename($tmp, __DIR__ . '/' . $name)) {
    @unlink($tmp);
    http_response_code(500);
    exit;
}
@unlink(__DIR__ . '/' . $purpose . '.las');    // the copy under its plain name, from before this layout: replaced
http_response_code(204);
