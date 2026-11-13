<?php
// Saves the client's picks, encrypted in their browser with the gallery key, as one file beside the gallery
// (picks.bin; the latest submission wins). Lumen Aureum fetches it on its regular gallery check and decrypts it there.
// It accepts only the exact encrypted envelope, never readable text: "LAK1", a 12-byte nonce, then AES-256-GCM
// ciphertext (at least its 16-byte tag), 64 KB at most. Only a visitor holding the key can write it: the X-Proof header
// is sha256 of the key (32 hex), checked against its SHA-256 in seen-key.php, the same proof seen.php checks.
header('Cache-Control: no-store');
header('X-Content-Type-Options: nosniff');
if ($_SERVER['REQUEST_METHOD'] !== 'POST') { http_response_code(405); exit; }
$raw = @file_get_contents(__DIR__ . '/seen-key.php');
$want = $raw === false ? false : trim(substr($raw, strpos($raw, "\n") + 1));
$proof = (string)($_SERVER['HTTP_X_PROOF'] ?? '');
if ($want === false || !hash_equals($want, hash('sha256', trim($proof)))) { http_response_code(403); exit; }
$len = (int)($_SERVER['CONTENT_LENGTH'] ?? 0);
if ($len < 4 + 12 + 16 || $len > 65536) { http_response_code(413); exit; }
$body = (string)file_get_contents('php://input', false, null, 0, 65536);
if (strlen($body) !== $len || substr($body, 0, 4) !== 'LAK1') { http_response_code(400); exit; }
// ciphertext looks like random bytes: refuse a body that is mostly readable text
$printable = preg_match_all('/[\x20-\x7e\t\r\n]/', substr($body, 16));
if ($printable > 0.85 * ($len - 16)) { http_response_code(400); exit; }
$tmp = __DIR__ . '/picks.bin.' . bin2hex(random_bytes(6)) . '.tmp';
if (@file_put_contents($tmp, $body, LOCK_EX) !== $len || !@rename($tmp, __DIR__ . '/picks.bin')) {
    @unlink($tmp);
    http_response_code(500);
    exit;
}
http_response_code(204);
