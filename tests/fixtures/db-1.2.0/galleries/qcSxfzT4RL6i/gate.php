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
{
    $seen = @file_get_contents(__DIR__ . '/seen.txt');
    $end = $seen ? strtotime(trim($seen)) + 86400 * (int)$lim['days']
                 : strtotime($lim['unopened_until'] . 'T23:59:59Z');
    if ($end === false || time() > $end) { http_response_code(410); exit; }   // an unreadable date closes it too
}
// Galleries whose page sends the key proof (limits.txt proof=1): every file needs it, so the encrypted files never
// leave the host for someone holding only the link, and a codeword can't be guessed offline. Older galleries (no
// proof=1) are served as before. The client's draft picks (draft.bin) and the picks they sent (picks.bin, read back so
// the page can tell what was really sent) always need the proof.
// No proof at all is refused at once (nothing to slow down, and no PHP worker is held). A wrong proof waits a second
// on purpose: it is a rate limit on guessing a codeword through the host, at the cost of one worker for that second.
if (!empty($lim['proof']) || $name === 'draft.bin' || $name === 'picks.bin') {
    $proof = trim((string)($_SERVER['HTTP_X_PROOF'] ?? ''));
    if ($proof === '') { http_response_code(403); exit; }
    $raw = @file_get_contents(__DIR__ . '/seen-key.php');
    $want = $raw === false ? false : trim(substr($raw, strpos($raw, "\n") + 1));
    if ($want === false || !hash_equals($want, hash('sha256', $proof))) { sleep(1); http_response_code(403); exit; }
}
$p = __DIR__ . '/' . $name;
if (!is_file($p)) { http_response_code(404); exit; }
header('Content-Type: application/octet-stream');
header('Content-Length: ' . filesize($p));
readfile($p);
