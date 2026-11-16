<?php
$SALT = 'unbuilt';   // random for each install, written in when the site is built (PHP source is never served)
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
$printable = preg_match_all('/[\x20-\x7e\t\r\n]/', substr($body, 81));
if ($printable > 0.85 * ($len - 81)) { http_response_code(400); exit; }
$h = hash('sha256', hash('sha256', $body, true) . hex2bin($w), true);
$need = max(0, min(32, (int)$WORK));
for ($i = 0; $need >= 8; $i++, $need -= 8) { if ($h[$i] !== "\0") { http_response_code(400); exit; } }
if ($need > 0 && (ord($h[$i]) >> (8 - $need)) !== 0) { http_response_code(400); exit; }
$dir = __DIR__ . '/inquiries';
if (!is_dir($dir)) { http_response_code(503); exit; }
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
if (count($seen) >= 5000 && !isset($seen[$who])) {
    $last = array_map('max', $seen);
    arsort($last);
    $seen = array_intersect_key($seen, array_slice($last, 0, 4000, true));
}
$out = function ($code) use ($fh) { flock($fh, LOCK_UN); fclose($fh); http_response_code($code); exit; };
if (count($seen[$who] ?? []) >= 5) { $out(429); }
$sum = substr(hash('sha256', $body), 0, 32);
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

