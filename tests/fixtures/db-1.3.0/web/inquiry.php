<?php
// Keeps one inquiry sent from the studio page's form, for Lumen Aureum to collect on its next check. The visitor's
// browser sealed it to the studio's public key (ECDH P-256, HKDF-SHA256, AES-256-GCM), so only the app, which holds the
// private key, can read it; nothing readable ever reaches this host, its folders or its logs.
// It accepts only the exact envelope, as one form field (d=, base64url, which web firewalls accept): "LAQ1", the
// browser's one-time public key (65 bytes, uncompressed point), a 12-byte nonce, then the ciphertext with its tag;
// 16 KB at most. With it, w=: 8 bytes (hex) the browser searched for, so that SHA-256(SHA-256(envelope) . w) starts
// with $WORK zero bits: about a second on a phone, so sealing inquiries in bulk costs real time.
// A few per hour from one sender (an IPv4 address, or an IPv6 /56: the block one subscriber is usually given), at most
// 10 waiting from one sender, and at most 200 waiting in all. When the folder is full, the newest inquiry of the sender
// with the most waiting makes room, so one network can never fill the folder and turn everyone else away.
// Each file is named <sender>-<content>.bin: the sender as a keyed hash (never the address itself).
$SALT = '2d2d0c8daa1b7355542ce51c71569d0d';   // random for each install, written in when the site is built (PHP source is never served)
$WORK = 17;          // leading zero bits the proof of work needs (the form carries the same number)
header('Cache-Control: no-store');
header('X-Content-Type-Options: nosniff');
if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST') { http_response_code(405); exit; }
$ct = strtolower(trim(explode(';', (string)($_SERVER['CONTENT_TYPE'] ?? ''))[0]));
if ($ct !== 'application/x-www-form-urlencoded') { http_response_code(415); exit; }
if ((int)($_SERVER['CONTENT_LENGTH'] ?? 0) > 22000) { http_response_code(413); exit; }
if (count($_GET) !== 0 || count($_POST) !== 2 || !isset($_POST['d'], $_POST['w']) || !is_string($_POST['d']) || !is_string($_POST['w'])) { http_response_code(400); exit; }
$d = $_POST['d'];
$w = $_POST['w'];
if (strlen($d) > 21848 || !preg_match('/^[A-Za-z0-9_-]+$/', $d)) { http_response_code(413); exit; }
if (!preg_match('/^[0-9a-f]{16}$/', $w)) { http_response_code(400); exit; }
$body = base64_decode(strtr($d, '-_', '+/'), true);
if ($body === false) { http_response_code(400); exit; }
$len = strlen($body);
if ($len < 4 + 65 + 12 + 16 + 2 || $len > 16384 || substr($body, 0, 4) !== 'LAQ1' || $body[4] !== "\x04") { http_response_code(400); exit; }
// ciphertext looks like random bytes: refuse a body that is mostly readable text
$printable = preg_match_all('/[\x20-\x7e\t\r\n]/', substr($body, 81));
if ($printable > 0.85 * ($len - 81)) { http_response_code(400); exit; }
// the proof of work: two hashes to check, however long it took to find
$h = hash('sha256', hash('sha256', $body, true) . hex2bin($w), true);
$need = max(0, min(32, (int)$WORK));
for ($i = 0; $need >= 8; $i++, $need -= 8) { if ($h[$i] !== "\0") { http_response_code(400); exit; } }
if ($need > 0 && (ord($h[$i]) >> (8 - $need)) !== 0) { http_response_code(400); exit; }
$dir = __DIR__ . '/inquiries';
if (!is_dir($dir)) { http_response_code(503); exit; }
// a few per hour from one sender, kept as a keyed hash (never the address itself), counted under a lock.
// An IPv6 address counts by its /56 (one subscriber's block holds 256 /64s); an IPv4 address by itself.
$ip = (string)($_SERVER['REMOTE_ADDR'] ?? '');
$bin = @inet_pton($ip);
if ($bin === false) { $net = $ip; }
elseif (strlen($bin) === 16 && substr($bin, 0, 12) === str_repeat("\0", 10) . "\xff\xff") { $net = substr($bin, 12); }
elseif (strlen($bin) === 16) { $net = substr($bin, 0, 7); }
else { $net = $bin; }
$grp = substr(hash_hmac('sha256', $net, $SALT), 0, 16);
$who = 'a' . $grp;
$fh = @fopen($dir . '/.rate', 'c+');
if ($fh === false || !flock($fh, LOCK_EX)) { http_response_code(503); exit; }
$now = time();
$seen = json_decode((string)stream_get_contents($fh), true);
if (!is_array($seen)) { $seen = []; }
foreach ($seen as $k => $ts) {
    $keep = is_array($ts) ? array_values(array_filter($ts, function ($t) use ($now) { return is_int($t) && $t > $now - 3600; })) : [];
    if ($keep && is_string($k)) { $seen[$k] = $keep; } else { unset($seen[$k]); }
}
// a full list never turns everyone away: the senders seen longest ago make room
if (count($seen) >= 5000 && !isset($seen[$who])) {
    $last = array_map('max', $seen);
    arsort($last);
    $seen = array_intersect_key($seen, array_slice($last, 0, 4000, true));
}
$out = function ($code) use ($fh) { flock($fh, LOCK_UN); fclose($fh); http_response_code($code); exit; };
if (count($seen[$who] ?? []) >= 5) { $out(429); }
// named by its content: the same envelope sent again is kept once
$sum = substr(hash('sha256', $body), 0, 32);
// what waits to be collected, by sender (a file from before senders were named counts as no one's, and is never
// moved aside): at most 10 from one sender, 200 in all, 4 MB
$n = 0; $total = 0; $by = [];
foreach (glob($dir . '/*.bin') ?: [] as $f) {
    $b = basename($f, '.bin');
    if (substr($b, -32) === $sum) { $out(204); }
    $n++; $sz = (int)@filesize($f); $total += $sz;
    if (preg_match('/^([0-9a-f]{16})-[0-9a-f]{32}$/', $b, $m)) { $by[$m[1]][] = [(int)@filemtime($f), $f, $sz]; }
}
$mine = count($by[$grp] ?? []);
if ($mine >= 10) { $out(429); }
while ($n >= 200 || $total + $len > 4 * 1024 * 1024) {
    // full: the sender with the most waiting gives up its newest, only while it has at least two more than this one
    $top = null;
    foreach ($by as $g => $fs) { if ((string)$g !== $grp && ($top === null || count($fs) > count($by[$top]))) { $top = $g; } }
    if ($top === null || count($by[$top]) < $mine + 2) { $out(507); }
    usort($by[$top], function ($a, $b) { return $b[0] <=> $a[0]; });
    $drop = array_shift($by[$top]);
    if (!@unlink($drop[1])) { $out(507); }
    $n--; $total -= $drop[2];
}
$seen[$who][] = $now;
ftruncate($fh, 0); rewind($fh); fwrite($fh, json_encode($seen)); fflush($fh);
$name = $grp . '-' . $sum;
$tmp = $dir . '/.' . bin2hex(random_bytes(16)) . '.tmp';
if (@file_put_contents($tmp, $body) !== $len || !@rename($tmp, $dir . '/' . $name . '.bin')) {
    @unlink($tmp);
    $out(500);
}
$out(204);
