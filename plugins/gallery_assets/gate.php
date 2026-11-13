<?php
// Serves the gallery's encrypted files only while the gallery is still within its window, so it expires on time even
// if Lumen Aureum is locked or switched off. The window: limits.txt (days after the first visit, or a date if the
// gallery is never opened), written by Lumen Aureum at upload and on an extension. The files are ciphertext either way.
header('Cache-Control: no-store');
header('X-Content-Type-Options: nosniff');
$name = basename((string)($_GET['f'] ?? ''));
if (!preg_match('/^([0-9a-f]{24}|manifest)\.bin$/', $name)) { http_response_code(404); exit; }
$lim = @parse_ini_file(__DIR__ . '/limits.txt');
// limits.txt is uploaded with the gallery and rewritten on every check: missing or unreadable means closed, never open
if ($lim === false || !isset($lim['days'], $lim['unopened_until'])) { http_response_code(410); exit; }
{
    $seen = @file_get_contents(__DIR__ . '/seen.txt');
    $end = $seen ? strtotime(trim($seen)) + 86400 * (int)$lim['days']
                 : strtotime($lim['unopened_until'] . 'T23:59:59Z');
    if ($end === false || time() > $end) { http_response_code(410); exit; }   // an unreadable date closes it too
}
$p = __DIR__ . '/' . $name;
if (!is_file($p)) { http_response_code(404); exit; }
header('Content-Type: application/octet-stream');
header('Content-Length: ' . filesize($p));
readfile($p);
