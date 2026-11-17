<?php
// Keeps one inquiry sent from the studio page's form, for Lumen Aureum to collect on its next check. The visitor's
// browser sealed it to the studio's public key (the shared sealed format, LAS1, purpose inquiry: ECDH P-256 with a
// one-time key, HKDF-SHA256, AES-256-GCM), so only the app, which holds the private key, can read it; nothing readable
// ever reaches this host, its folders or its logs.
// It accepts only that envelope, as one form field (d=, base64url without padding, which web firewalls accept), with
// the studio key's ID ($KID) in its header, 16 KB at most, and not readable text (sealed.php checks its shape). With
// it, w=: 8 bytes (hex) the browser searched for, so that SHA-256(SHA-256(envelope) . w) starts with $WORK zero bits:
// about a second on a phone, so sealing inquiries in bulk costs real time.
// A few per hour from one sender (an IPv4 address, or an IPv6 /56: the block one subscriber is usually given), at most
// 10 waiting from one sender, and at most 200 waiting in all. When the folder is full, the newest inquiry of the sender
// with the most waiting makes room, so one network can never fill the folder and turn everyone else away.
// Past a sender's limits nobody is simply turned away: the answer is 429 with X-Work naming more work ($WORK_SHARED
// bits, a few seconds in the browser), which the form works out and sends again. So a visitor who shares an address
// with others (an office, a phone network, a proxy the web host check didn't see) still gets through, and a script
// sending many pays for each one. Behind a proxy or CDN the check did see ($SHARED = 1) no sender is counted at all
// (every visitor seems to come from the same few addresses), and every inquiry needs the larger work; there a full
// folder refuses more (507) rather than drop an inquiry already kept.
// The rate list is inquiries/.rate.php, which begins with a PHP exit line, so fetching it shows nothing; the folder
// holds an empty index.html, so a web server that lists folders shows nothing there either.
// Each file is named <sender>-<content>.las: the sender as a keyed hash (never the address itself). Files of any other
// name in the folder are never counted, listed, replaced or removed here.
header('X-Host-Scripts: 1.4.0');
$SALT = 'unbuilt';   // random for each install, written in when the site is built (PHP source is never served)
$WORK = 17;          // leading zero bits the proof of work needs (the form carries the same number)
$KID = '';           // the studio key's ID (16 hex), written in when the site is built
$SHARED = 0;         // 1: the host is behind a proxy or CDN (the web host check), written in when the site is built
$WORK_SHARED = 20;   // leading zero bits each inquiry needs there
header('Cache-Control: no-store');
header('X-Content-Type-Options: nosniff');
if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST') { http_response_code(405); exit; }
$ct = strtolower(trim(explode(';', (string)($_SERVER['CONTENT_TYPE'] ?? ''))[0]));
if ($ct !== 'application/x-www-form-urlencoded') { http_response_code(415); exit; }
if ((int)($_SERVER['CONTENT_LENGTH'] ?? 0) > 22000) { http_response_code(413); exit; }
if (count($_GET) !== 0 || count($_POST) !== 2 || !isset($_POST['d'], $_POST['w']) || !is_string($_POST['d']) || !is_string($_POST['w'])) { http_response_code(400); exit; }
$d = $_POST['d'];
$w = $_POST['w'];
if (!preg_match('/^[0-9a-f]{16}$/', $w) || !preg_match('/^[0-9a-f]{16}$/', $KID)) { http_response_code(400); exit; }
require __DIR__ . '/sealed.php';
list($body, $err) = sealed_decode($d, 'inquiry');
if ($err === '') { $err = sealed_host_check($body, 'inquiry', '', hex2bin($KID)); }
if ($err !== '') { http_response_code(sealed_status($err)); exit; }
$len = strlen($body);
// the proof of work: two hashes to check, however long it took to find
$h = hash('sha256', hash('sha256', $body, true) . hex2bin($w), true);
$need = max(0, min(32, (int)$WORK));
for ($i = 0; $need >= 8; $i++, $need -= 8) { if ($h[$i] !== "\0") { http_response_code(400); exit; } }
if ($need > 0 && (ord($h[$i]) >> (8 - $need)) !== 0) { http_response_code(400); exit; }
$dir = __DIR__ . '/inquiries';
if (!is_dir($dir)) { http_response_code(503); exit; }
// whether the work also meets the larger number of bits (past a sender's limits, or behind a proxy)
$need = max(0, min(32, (int)$WORK_SHARED));
$strong = true;
for ($i = 0; $need >= 8; $i++, $need -= 8) { if ($h[$i] !== "\0") { $strong = false; break; } }
if ($strong && $need > 0 && (ord($h[$i]) >> (8 - $need)) !== 0) { $strong = false; }
if ($SHARED && !$strong) { http_response_code(429); header('X-Work: ' . (int)$WORK_SHARED); exit; }
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
$fh = @fopen($dir . '/.rate.php', 'c+');
if ($fh === false || !flock($fh, LOCK_EX)) { http_response_code(503); exit; }
@unlink($dir . '/.rate');            // the plain list from before (readable on a web server that ignores .htaccess)
$now = time();
$raw = (string)stream_get_contents($fh);
$seen = json_decode((string)substr($raw, (int)strpos($raw, "\n") + 1), true);
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
// past this sender's limits: more work asked for, never a flat refusal
$more = function () use ($out, $WORK_SHARED) { header('X-Work: ' . (int)$WORK_SHARED); $out(429); };
if (!$SHARED && count($seen[$who] ?? []) >= 5 && !$strong) { $more(); }
// named by its content: the same envelope sent again is kept once
$sum = substr(hash('sha256', $body), 0, 32);
// what waits to be collected, by sender (a file from before senders were named counts as no one's, and is never
// moved aside): at most 10 from one sender, 200 in all, 4 MB
$n = 0; $total = 0; $by = [];
foreach (glob($dir . '/*.las') ?: [] as $f) {
    $b = basename($f, '.las');
    if (substr($b, -32) === $sum) { $out(204); }
    $n++; $sz = (int)@filesize($f); $total += $sz;
    if (preg_match('/^([0-9a-f]{16})-[0-9a-f]{32}$/', $b, $m)) { $by[$m[1]][] = [(int)@filemtime($f), $f, $sz]; }
}
$mine = count($by[$grp] ?? []);
if (!$SHARED && $mine >= 10 && !$strong) { $more(); }
while ($n >= 200 || $total + $len > 4 * 1024 * 1024) {
    // full: the sender with the most waiting gives up its newest, only while it has at least two more than this one
    // (behind a proxy, where senders mean nothing, nothing kept is dropped: refused)
    if ($SHARED) { $out(507); }
    $top = null;
    foreach ($by as $g => $fs) { if ((string)$g !== $grp && ($top === null || count($fs) > count($by[$top]))) { $top = $g; } }
    if ($top === null || count($by[$top]) < $mine + 2) { $out(507); }
    usort($by[$top], function ($a, $b) { return $b[0] <=> $a[0]; });
    $drop = array_shift($by[$top]);
    if (!@unlink($drop[1])) { $out(507); }
    $n--; $total -= $drop[2];
}
$seen[$who][] = $now;
if (!$SHARED) { ftruncate($fh, 0); rewind($fh); fwrite($fh, "<?php exit; ?>\n" . json_encode($seen) . "\n"); fflush($fh); }
$name = $grp . '-' . $sum;
$tmp = $dir . '/.' . bin2hex(random_bytes(16)) . '.tmp';
if (@file_put_contents($tmp, $body) !== $len || !@rename($tmp, $dir . '/' . $name . '.las')) {
    @unlink($tmp);
    $out(500);
}
$out(204);
