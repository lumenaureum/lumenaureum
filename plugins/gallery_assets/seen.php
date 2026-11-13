<?php
// Records the first visit once. The timestamp file is the only thing it ever writes.
header('Cache-Control: no-store');
$f = __DIR__ . '/seen.txt';
// Only a visitor holding the key can stamp it: the body is sha256 of the key (32 hex), and its own SHA-256 must match
// the one in seen-key.php (the host never holds the proof itself), so knowing the gallery address alone can't start
// the clock early.
$raw = @file_get_contents(__DIR__ . '/seen-key.php');
$want = $raw === false ? false : trim(substr($raw, strpos($raw, "\n") + 1));
$got = substr((string)file_get_contents('php://input'), 0, 64);
if ($_SERVER['REQUEST_METHOD'] === 'POST' && !file_exists($f) && $want !== false && hash_equals($want, hash('sha256', trim($got)))) {
    $h = @fopen($f, 'x');
    if ($h) {
        fwrite($h, gmdate('Y-m-d\TH:i:s\Z'));
        fclose($h);
    }
}
http_response_code(204);
