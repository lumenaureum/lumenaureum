<?php
header('Cache-Control: no-store');
$f = __DIR__ . '/seen.txt';
$raw = @file_get_contents(__DIR__ . '/seen-key.php');
$want = $raw === false ? false : trim(substr($raw, strpos($raw, "\n") + 1));
$got = isset($_POST['p']) && is_string($_POST['p']) ? substr($_POST['p'], 0, 64)
     : (string)file_get_contents('php://input', false, null, 0, 64);
if (file_exists($f) && strtotime(trim((string)@file_get_contents($f))) === false && time() - (int)@filemtime($f) > 30) {
    @unlink($f);
}
if ($_SERVER['REQUEST_METHOD'] === 'POST' && !file_exists($f) && $want !== false && hash_equals($want, hash('sha256', trim($got)))) {
    $tmp = $f . '.' . bin2hex(random_bytes(6)) . '.tmp';
    if (@file_put_contents($tmp, gmdate('Y-m-d\TH:i:s\Z'), LOCK_EX) !== false) {
        if (!@link($tmp, $f) && !file_exists($f)) { @rename($tmp, $f); }
    }
    @unlink($tmp);
}
http_response_code(204);

