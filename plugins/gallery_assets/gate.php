<?php
header('Cache-Control: no-store');
header('X-Content-Type-Options: nosniff');
$name = basename((string)($_GET['f'] ?? ''));
if (!preg_match('/^([0-9a-f]{24}|manifest|draft|picks)\.bin$/', $name)) { http_response_code(404); exit; }
$lim = @parse_ini_file(__DIR__ . '/limits.txt');
if ($lim === false || !isset($lim['days'], $lim['unopened_until'])) { http_response_code(410); exit; }
function dark($lim) {
    http_response_code(410);
    $c = isset($lim['contact']) ? (string)$lim['contact'] : '';
    if ($c !== '' && strlen($c) <= 254 && filter_var($c, FILTER_VALIDATE_EMAIL) && !preg_match('/["\'<>\\\\]/', $c)) {
        header('Content-Type: application/json');
        echo json_encode(['contact' => $c]);
    }
    exit;
}
function day_end($lim, $ts, $day) {
    if (isset($lim[$ts]) && ctype_digit((string)$lim[$ts])) { return (int)$lim[$ts]; }
    return isset($lim[$day]) ? strtotime($lim[$day] . 'T23:59:59Z') : false;
}
{
    $seen = @file_get_contents(__DIR__ . '/seen.txt');
    $end = $seen ? strtotime(trim($seen)) + 86400 * (int)$lim['days'] : day_end($lim, 'unopened_ts', 'unopened_until');
    if (isset($lim['until']) || isset($lim['until_ts'])) { $end = day_end($lim, 'until_ts', 'until'); }
    $done = @file_get_contents(__DIR__ . '/done.txt');
    if ($done !== false && ctype_digit(trim($done)) && (int)trim($done) < $end) { $end = (int)trim($done); }
    if ($end === false || time() > $end) { dark($lim); }   // an unreadable date closes it too
    $warn = isset($lim['warn_days']) ? (int)$lim['warn_days'] : 0;
    $sunset = $warn > 0 && $end - time() <= 86400 * $warn ? gmdate('Y-m-d\TH:i:s\Z', $end) : '';
}
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
$proved = false;
if (!empty($lim['proof']) || $name === 'draft.bin' || $name === 'picks.bin') {
    $proof = trim((string)($_SERVER['HTTP_X_PROOF'] ?? ''));
    if ($proof === '') { http_response_code(403); exit; }
    $raw = @file_get_contents(__DIR__ . '/seen-key.php');
    $want = $raw === false ? false : trim(substr($raw, strpos($raw, "\n") + 1));
    if ($want === false || !proof_ok($want, $proof)) { http_response_code(403); exit; }
    $proved = true;
}
$p = __DIR__ . '/' . $name;
if (!is_file($p)) { http_response_code(404); exit; }
if ($sunset !== '') { header('X-Sunset: ' . $sunset); }
if ($name === 'manifest.bin' && $proved) {
    $have = array_values(array_filter(['draft', 'picks'], function ($n) { return is_file(__DIR__ . '/' . $n . '.bin'); }));
    header('X-Saved: ' . ($have ? implode(',', $have) : 'none'));
}
$once = isset($lim['once']) ? array_filter(explode(',', (string)$lim['once']), function ($n) { return preg_match('/^[0-9a-f]{24}\.bin$/', $n); }) : [];
if ($once) {
    if ($name === 'manifest.bin') {               // the page says: one download (and, once it was made, that it was)
        header('X-Once: ' . gmdate('Y-m-d\TH:i:s\Z', $end));
        if ($done !== false && ctype_digit(trim($done))) { header('X-Once-Done: 1'); }
    }
    if (in_array($name, $once, true)) {         // a piece of Download all: noted; the last one starts the hour
        $f = fopen(__DIR__ . '/once.txt', 'c+');
        if ($f && flock($f, LOCK_EX)) {
            $got = array_filter(explode("\n", stream_get_contents($f)));
            if (!in_array($name, $got, true)) { $got[] = $name; ftruncate($f, 0); rewind($f); fwrite($f, implode("\n", $got) . "\n"); }
            if (!array_diff($once, $got) && !file_exists(__DIR__ . '/done.txt')) { @file_put_contents(__DIR__ . '/done.txt', (string)(time() + 3600)); }
            flock($f, LOCK_UN);
        }
        if ($f) { fclose($f); }
    }
}
header('Content-Type: application/octet-stream');
header('Content-Length: ' . filesize($p));
readfile($p);

