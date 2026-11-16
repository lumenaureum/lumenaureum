<?php
header('Cache-Control: no-store');
header('X-Content-Type-Options: nosniff');
if ($_SERVER['REQUEST_METHOD'] !== 'POST') { http_response_code(405); exit; }
$kind = (string)($_GET['kind'] ?? '');
if ($kind !== '' && $kind !== 'draft') { http_response_code(400); exit; }
$name = $kind === 'draft' ? 'draft.bin' : 'picks.bin';
$lim = @parse_ini_file(__DIR__ . '/limits.txt');
if ($lim === false || !isset($lim['days'], $lim['unopened_until'])) { http_response_code(410); exit; }
function day_end($lim, $ts, $day) {             // as in gate.php: the exact instant, else the date's end in UTC (before 1.3)
    if (isset($lim[$ts]) && ctype_digit((string)$lim[$ts])) { return (int)$lim[$ts]; }
    return isset($lim[$day]) ? strtotime($lim[$day] . 'T23:59:59Z') : false;
}
$seen = @file_get_contents(__DIR__ . '/seen.txt');
$end = $seen ? strtotime(trim($seen)) + 86400 * (int)$lim['days'] : day_end($lim, 'unopened_ts', 'unopened_until');
if (isset($lim['until']) || isset($lim['until_ts'])) { $end = day_end($lim, 'until_ts', 'until'); }   // put back online: a fixed last day
if ($end === false || time() > $end) { http_response_code(410); exit; }
$raw = @file_get_contents(__DIR__ . '/seen-key.php');
$want = $raw === false ? false : trim(substr($raw, strpos($raw, "\n") + 1));
$proof = (string)($_SERVER['HTTP_X_PROOF'] ?? '');
if ($want === false || !hash_equals($want, hash('sha256', trim($proof)))) { http_response_code(403); exit; }
if (!empty($lim['moved'])) {
    http_response_code(409);
    header('Content-Type: application/json');
    $c = isset($lim['contact']) ? (string)$lim['contact'] : '';
    $ok = $c !== '' && strlen($c) <= 254 && filter_var($c, FILTER_VALIDATE_EMAIL) && !preg_match('/["\'<>\\\\]/', $c);
    echo json_encode($ok ? ['moved' => 1, 'contact' => $c] : ['moved' => 1]);
    exit;
}
if (isset($_POST['d']) && is_string($_POST['d'])) {
    if (strlen($_POST['d']) > 349528 || !preg_match('/^[A-Za-z0-9_-]*={0,2}$/', $_POST['d'])) { http_response_code(413); exit; }
    $body = (string)base64_decode(strtr($_POST['d'], '-_', '+/'), true);
    $len = strlen($body);
    if ($len < 4 + 12 + 16 || $len > 262144) { http_response_code(413); exit; }
} else {
    $len = (int)($_SERVER['CONTENT_LENGTH'] ?? 0);
    if ($len < 4 + 12 + 16 || $len > 262144) { http_response_code(413); exit; }
    $body = (string)file_get_contents('php://input', false, null, 0, 262144);
}
if (strlen($body) !== $len || substr($body, 0, 4) !== 'LAK1') { http_response_code(400); exit; }
$printable = preg_match_all('/[\x20-\x7e\t\r\n]/', substr($body, 16));
if ($printable > 0.85 * ($len - 16)) { http_response_code(400); exit; }
$tmp = __DIR__ . '/' . $name . '.' . bin2hex(random_bytes(6)) . '.tmp';
if (@file_put_contents($tmp, $body, LOCK_EX) !== $len || !@rename($tmp, __DIR__ . '/' . $name)) {
    @unlink($tmp);
    http_response_code(500);
    exit;
}
http_response_code(204);

