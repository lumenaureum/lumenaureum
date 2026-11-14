<?php
header('Cache-Control: no-store');
header('X-Content-Type-Options: nosniff');
$name = basename((string)($_GET['f'] ?? ''));
if (!preg_match('/^([0-9a-f]{24}|manifest|draft)\.bin$/', $name)) { http_response_code(404); exit; }
$lim = @parse_ini_file(__DIR__ . '/limits.txt');
if ($lim === false || !isset($lim['days'], $lim['unopened_until'])) { http_response_code(410); exit; }
{
    $seen = @file_get_contents(__DIR__ . '/seen.txt');
    $end = $seen ? strtotime(trim($seen)) + 86400 * (int)$lim['days']
                 : strtotime($lim['unopened_until'] . 'T23:59:59Z');
    if ($end === false || time() > $end) { http_response_code(410); exit; }   // an unreadable date closes it too
}
if (!empty($lim['proof']) || $name === 'draft.bin') {
    $raw = @file_get_contents(__DIR__ . '/seen-key.php');
    $want = $raw === false ? false : trim(substr($raw, strpos($raw, "\n") + 1));
    $proof = trim((string)($_SERVER['HTTP_X_PROOF'] ?? ''));
    if ($want === false || !hash_equals($want, hash('sha256', $proof))) { sleep(1); http_response_code(403); exit; }
}
$p = __DIR__ . '/' . $name;
if (!is_file($p)) { http_response_code(404); exit; }
header('Content-Type: application/octet-stream');
header('Content-Length: ' . filesize($p));
readfile($p);

