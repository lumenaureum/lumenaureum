<?php
// Saves the client's picks, encrypted in their browser with the gallery key, as one file beside the gallery
// (picks.bin; the latest submission wins). With ?kind=draft it saves the picks still being made instead (draft.bin, the
// same checks), so the client can carry on from another device and the app can show they are still picking. Lumen Aureum fetches it on its regular gallery check and decrypts it there.
// It accepts only the exact encrypted envelope, never readable text: "LAK1", a 12-byte nonce, then AES-256-GCM
// ciphertext (at least its 16-byte tag), 256 KB at most. Only a visitor holding the key can write it: the X-Proof header
// is sha256 of the key (32 hex), checked against its SHA-256 in seen-key.php, the same proof seen.php checks.
header('Cache-Control: no-store');
header('X-Content-Type-Options: nosniff');
if ($_SERVER['REQUEST_METHOD'] !== 'POST') { http_response_code(405); exit; }
$kind = (string)($_GET['kind'] ?? '');
if ($kind !== '' && $kind !== 'draft') { http_response_code(400); exit; }
$name = $kind === 'draft' ? 'draft.bin' : 'picks.bin';
// The same window as gate.php: once the gallery has closed, nothing more is written (missing or unreadable limits.txt
// means closed, never open; so does an unreadable date).
$lim = @parse_ini_file(__DIR__ . '/limits.txt');
if ($lim === false || !isset($lim['days'], $lim['unopened_until'])) { http_response_code(410); exit; }
function day_end($lim, $ts, $day) {             // as in gate.php: the exact instant, else the date's end in UTC (before 1.3)
    if (isset($lim[$ts]) && ctype_digit((string)$lim[$ts])) { return (int)$lim[$ts]; }
    return isset($lim[$day]) ? strtotime($lim[$day] . 'T23:59:59Z') : false;
}
$seen = @file_get_contents(__DIR__ . '/seen.txt');
$end = $seen ? strtotime(trim($seen)) + 86400 * (int)$lim['days'] : day_end($lim, 'unopened_ts', 'unopened_until');
if (isset($lim['until']) || isset($lim['until_ts'])) { $end = day_end($lim, 'until_ts', 'until'); }   // put back online: a fixed last day
if ($end === false || time() > $end) { http_response_code(410); exit; }
$raw = @file_get_contents(__DIR__ . '/seen-key.php');
$want = $raw === false ? false : trim(substr($raw, strpos($raw, "\n") + 1));
// As in gate.php: wrong key proofs are counted per sender (an IPv4 address, or an IPv6 /56, as inquiry.php counts) in tries.php in this
// folder, shared by gate.php and picks.php: after 10 wrong proofs in an hour, every proof from that sender is refused
// (429) without being checked, until its oldest wrong proof is an hour old. Nothing ever waits, so a script of wrong
// proofs can't tie up the host's PHP workers. The check and the count happen under one lock, so a burst of requests
// gets no more answers than one at a time. tries.php starts with a PHP exit line, so fetching it shows nothing, and keeps
// each sender as a hash keyed by this gallery's proof hash, never the address itself.
function proof_ok($want, $proof) {
    $ip = (string)($_SERVER['REMOTE_ADDR'] ?? '');
    $bin = @inet_pton($ip);
    if ($bin === false) { $net = $ip; }
    elseif (strlen($bin) === 16 && substr($bin, 0, 12) === str_repeat("\0", 10) . "\xff\xff") { $net = substr($bin, 12); }
    elseif (strlen($bin) === 16) { $net = substr($bin, 0, 7); }
    else { $net = $bin; }
    $who = 'a' . substr(hash_hmac('sha256', $net, $want), 0, 16);
    $now = time();
    $fh = @fopen(__DIR__ . '/tries.php', 'c+');
    if ($fh !== false && !flock($fh, LOCK_EX)) { fclose($fh); $fh = false; }
    $all = [];
    if ($fh !== false) {
        $raw = (string)stream_get_contents($fh);
        $all = json_decode((string)substr($raw, (int)strpos($raw, "\n") + 1), true);
        if (!is_array($all)) { $all = []; }
        foreach ($all as $k => $ts) {
            $keep = is_array($ts) ? array_values(array_filter($ts, function ($t) use ($now) { return is_int($t) && $t > $now - 3600; })) : [];
            if ($keep && is_string($k)) { $all[$k] = $keep; } else { unset($all[$k]); }
        }
    }
    $mine = $all[$who] ?? [];
    if (count($mine) >= 10) {
        flock($fh, LOCK_UN); fclose($fh);
        http_response_code(429); header('Retry-After: ' . max(1, min($mine) + 3600 - $now)); exit;
    }
    $ok = hash_equals($want, hash('sha256', $proof));
    if (!$ok && $fh !== false) {
        $mine[] = $now;
        $all[$who] = $mine;
        if (count($all) > 2000) {                   // a full list: the senders seen longest ago make room
            $last = array_map('max', $all);
            arsort($last);
            $all = array_intersect_key($all, array_slice($last, 0, 1500, true));
        }
        ftruncate($fh, 0); rewind($fh); fwrite($fh, "<?php exit; ?>\n" . json_encode($all) . "\n"); fflush($fh);
    }
    if ($fh !== false) { flock($fh, LOCK_UN); fclose($fh); }
    return $ok;
}
$proof = trim((string)($_SERVER['HTTP_X_PROOF'] ?? ''));
if ($proof === '' || $want === false || !proof_ok($want, $proof)) { http_response_code(403); exit; }
// Moved to another gallery host (limits.txt moved=1, written by Move galleries): this copy is still there to look at
// until the photographer takes it down, but picks sent here would never be read. Refused, with the photographer's email.
if (!empty($lim['moved'])) {
    http_response_code(409);
    header('Content-Type: application/json');
    $c = isset($lim['contact']) ? (string)$lim['contact'] : '';
    $ok = $c !== '' && strlen($c) <= 254 && filter_var($c, FILTER_VALIDATE_EMAIL) && !preg_match('/["\'<>\\\\]/', $c);
    echo json_encode($ok ? ['moved' => 1, 'contact' => $c] : ['moved' => 1]);
    exit;
}
// The page sends the envelope as a form field (d=, base64url), which web firewalls accept; pages built before 1.2 sent
// the raw bytes as the body.
if (isset($_POST['d']) && is_string($_POST['d'])) {
    if (strlen($_POST['d']) > 349528 || !preg_match('/^[A-Za-z0-9_-]*={0,2}$/', $_POST['d'])) { http_response_code(413); exit; }
    $body = (string)base64_decode(strtr($_POST['d'], '-_', '+/'), true);
    $len = strlen($body);
    if ($len < 4 + 12 + 16 || $len > 262144) { http_response_code(413); exit; }
} else {
    $len = (int)($_SERVER['CONTENT_LENGTH'] ?? 0);
    if ($len < 4 + 12 + 16 || $len > 262144) { http_response_code(413); exit; }
    $body = (string)file_get_contents('php://input', false, null, 0, 262144);
}
if (strlen($body) !== $len || substr($body, 0, 4) !== 'LAK1') { http_response_code(400); exit; }
// ciphertext looks like random bytes: refuse a body that is mostly readable text
$printable = preg_match_all('/[\x20-\x7e\t\r\n]/', substr($body, 16));
if ($printable > 0.85 * ($len - 16)) { http_response_code(400); exit; }
$tmp = __DIR__ . '/' . $name . '.' . bin2hex(random_bytes(6)) . '.tmp';
if (@file_put_contents($tmp, $body, LOCK_EX) !== $len || !@rename($tmp, __DIR__ . '/' . $name)) {
    @unlink($tmp);
    http_response_code(500);
    exit;
}
http_response_code(204);
