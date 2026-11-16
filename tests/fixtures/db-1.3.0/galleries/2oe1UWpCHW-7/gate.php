<?php
// Serves the gallery's encrypted files only while the gallery is still within its window, so it expires on time even
// if Lumen Aureum is locked or switched off. The window: limits.txt (days after the first visit, or a date if the
// gallery is never opened), written by Lumen Aureum at upload and on an extension. The files are ciphertext either way.
header('Cache-Control: no-store');
header('X-Content-Type-Options: nosniff');
$name = basename((string)($_GET['f'] ?? ''));
if (!preg_match('/^([0-9a-f]{24}|manifest|draft|picks)\.bin$/', $name)) { http_response_code(404); exit; }
$lim = @parse_ini_file(__DIR__ . '/limits.txt');
// limits.txt is uploaded with the gallery and rewritten on every check: missing or unreadable means closed, never open
if ($lim === false || !isset($lim['days'], $lim['unopened_until'])) { http_response_code(410); exit; }
// Closed: the gallery has gone dark. The answer names the photographer's email (limits.txt contact, from Settings), the
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
// A fixed last day closes at the exact instant Lumen Aureum wrote (limits.txt unopened_ts, until_ts: the end of that day
// in the photographer's time zone, as a Unix time). Galleries written before 1.3 have only the date: the end of it in UTC.
function day_end($lim, $ts, $day) {
    if (isset($lim[$ts]) && ctype_digit((string)$lim[$ts])) { return (int)$lim[$ts]; }
    return isset($lim[$day]) ? strtotime($lim[$day] . 'T23:59:59Z') : false;
}
{
    $seen = @file_get_contents(__DIR__ . '/seen.txt');
    $end = $seen ? strtotime(trim($seen)) + 86400 * (int)$lim['days'] : day_end($lim, 'unopened_ts', 'unopened_until');
    // put back online after it went dark: a fixed last day (limits.txt until), whatever the visits
    if (isset($lim['until']) || isset($lim['until_ts'])) { $end = day_end($lim, 'until_ts', 'until'); }
    // a one-time download (limits.txt once: the Download all pieces): once every piece has been sent, it closes an
    // hour later (time to finish saving), whatever its last day
    $done = @file_get_contents(__DIR__ . '/done.txt');
    if ($done !== false && ctype_digit(trim($done)) && (int)trim($done) < $end) { $end = (int)trim($done); }
    if ($end === false || time() > $end) { dark($lim); }   // an unreadable date closes it too
    // In its last days (limits.txt warn_days) the page says the gallery is about to sunset, and when it closes
    // (sent with the file itself, so only a visitor the gate serves learns the date)
    $warn = isset($lim['warn_days']) ? (int)$lim['warn_days'] : 0;
    $sunset = $warn > 0 && $end - time() <= 86400 * $warn ? gmdate('Y-m-d\TH:i:s\Z', $end) : '';
}
// Galleries whose page sends the key proof (limits.txt proof=1): every file needs it, so the encrypted files never
// leave the host for someone holding only the link, and a codeword can't be guessed offline. Older galleries (no
// proof=1) are served as before. The client's draft picks (draft.bin) and the picks they sent (picks.bin, read back so
// the page can tell what was really sent) always need the proof.
// No proof at all is refused at once (nothing to slow down, and no PHP worker is held). A wrong proof waits a second
// on purpose: it is a rate limit on guessing a codeword through the host, at the cost of one worker for that second.
$proved = false;
if (!empty($lim['proof']) || $name === 'draft.bin' || $name === 'picks.bin') {
    $proof = trim((string)($_SERVER['HTTP_X_PROOF'] ?? ''));
    if ($proof === '') { http_response_code(403); exit; }
    $raw = @file_get_contents(__DIR__ . '/seen-key.php');
    $want = $raw === false ? false : trim(substr($raw, strpos($raw, "\n") + 1));
    if ($want === false || !hash_equals($want, hash('sha256', $proof))) { sleep(1); http_response_code(403); exit; }
    $proved = true;
}
$p = __DIR__ . '/' . $name;
if (!is_file($p)) { http_response_code(404); exit; }
if ($sunset !== '') { header('X-Sunset: ' . $sunset); }
// With the manifest, for a visitor who proved the key: whether the client's draft and sent picks are here, so the page
// only asks for the ones that are (no request answered "not found" on every visit)
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
