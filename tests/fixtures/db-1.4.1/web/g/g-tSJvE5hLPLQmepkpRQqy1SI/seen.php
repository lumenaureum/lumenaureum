<?php
// Records the first visit once. The timestamp file is the only thing it ever writes: seen-at.php, which begins with a
// PHP exit line, so fetching it shows nothing (a gallery from before this layout has seen.txt, which still counts).
header('X-Host-Scripts: 1.4.0');
header('Cache-Control: no-store');
$f = __DIR__ . '/seen-at.php';
$old = __DIR__ . '/seen.txt';
// Only a visitor holding the key can stamp it: the body is sha256 of the key (32 hex), and its own SHA-256 must match
// the one in seen-key.php (the host never holds the proof itself), so knowing the gallery address alone can't start
// the clock early.
$raw = @file_get_contents(__DIR__ . '/seen-key.php');
$want = $raw === false ? false : trim(substr($raw, strpos($raw, "\n") + 1));
// The page sends the proof as a form field (p=...), which web firewalls accept.
$got = isset($_POST['p']) && is_string($_POST['p']) ? substr($_POST['p'], 0, 64) : '';
// a stamp left empty or unreadable (a crash between creating and writing it) would hold the gallery open for its
// unopened period: it is replaced
foreach ([$f, $old] as $x) {
    if (!file_exists($x)) { continue; }
    $s = (string)@file_get_contents($x);
    if ($x === $f) { $s = strncmp($s, "<?php exit; ?>\n", 15) === 0 ? substr($s, 15) : ''; }
    if (strtotime(trim($s)) === false && time() - (int)@filemtime($x) > 30) { @unlink($x); }
}
if ($_SERVER['REQUEST_METHOD'] === 'POST' && !file_exists($f) && !file_exists($old) && $want !== false
    && hash_equals($want, hash('sha256', trim($got)))) {
    $tmp = $f . '.' . bin2hex(random_bytes(6)) . '.tmp';
    if (@file_put_contents($tmp, "<?php exit; ?>\n" . gmdate('Y-m-d\TH:i:s\Z'), LOCK_EX) !== false) {
        // link() never replaces: someone who stamped it first stands. Hosts without hard links get an atomic rename.
        if (!@link($tmp, $f) && !file_exists($f)) { @rename($tmp, $f); }
    }
    @unlink($tmp);
}
http_response_code(204);
