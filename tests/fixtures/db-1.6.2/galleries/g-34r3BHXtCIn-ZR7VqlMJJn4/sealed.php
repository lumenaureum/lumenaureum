<?php
// The shared sealed format, LAS1 (docs/sealed-format.md), as the web host checks it: shape only. The host never holds
// a key that opens anything, so nothing here decrypts. Loaded by the host scripts beside it (require __DIR__ .
// '/sealed.php'); fetched on its own it defines functions and prints nothing. Kept to syntax PHP 7.4 parses.
// The answers equal sealed.py's and sealed.js's for the same bytes (tests/vectors/sealed-v1.json).

// code, mode, largest envelope, the link kinds it may be sealed for ('' for an inquiry: it has no link ID)
const SEALED_PURPOSES = ['picks' => [1, 1, 262144, 'g'], 'draft' => [2, 1, 262144, 'g'],
                         'inquiry' => [3, 2, 16384, ''], 'print-order' => [4, 3, 65536, 'g']];

// The form field d (base64url, no padding) -> [bytes, ''] or [null, error]. A string longer than the purpose's largest
// envelope could encode to is E_LONG before it is decoded.
function sealed_decode($d, $purpose) {
    if (!is_string($purpose) || !array_key_exists($purpose, SEALED_PURPOSES)) { return [null, 'E_PURPOSE']; }
    if (!is_string($d)) { return [null, 'E_ENCODING']; }
    $m = SEALED_PURPOSES[$purpose][2];
    $tail = [0, 2, 3];
    if (strlen($d) > intdiv($m, 3) * 4 + $tail[$m % 3]) { return [null, 'E_LONG']; }
    if ($d === '' || !preg_match('/^[A-Za-z0-9_-]+\z/', $d) || strlen($d) % 4 === 1) { return [null, 'E_ENCODING']; }
    $s = strtr($d, '-_', '+/');
    $b = base64_decode($s . str_repeat('=', (4 - strlen($s) % 4) % 4), true);
    if ($b === false || rtrim(strtr(base64_encode($b), '+/', '-_'), '=') !== $d) { return [null, 'E_ENCODING']; }
    return [$b, ''];
}

// Steps 1 to 15 of the check, in order: '' or the error. $gid: the link ID expected ('' for an inquiry). $kid: the
// key ID expected, as 8 bytes or 16 hex characters, or null (then the key ID is not compared).
function sealed_check($env, $purpose, $gid, $kid) {
    if (!is_string($purpose) || !array_key_exists($purpose, SEALED_PURPOSES)) { return 'E_PURPOSE'; }
    if (!is_string($env)) { return 'E_SHORT'; }
    $p = SEALED_PURPOSES[$purpose];
    $len = strlen($env);
    if ($len < 4) { return 'E_SHORT'; }
    if (substr($env, 0, 3) !== 'LAS') { return 'E_MAGIC'; }
    if ($env[3] !== '1') { return 'E_VERSION'; }
    if ($len > $p[2]) { return 'E_LONG'; }
    if ($len < 7) { return 'E_SHORT'; }
    $mode = ord($env[4]);
    $n = ord($env[6]);
    if ($mode < 1 || $mode > 3) { return 'E_MODE'; }
    if (ord($env[5]) !== $p[0]) { return 'E_PURPOSE'; }
    if ($mode !== $p[1]) { return 'E_MODE'; }
    if ($mode === 2 ? $n !== 0 : $n !== 25) { return 'E_SCOPE'; }
    if ($len < 7 + $n) { return 'E_SHORT'; }
    $id = (string)substr($env, 7, $n);
    if (!preg_match($n ? '/^[A-Za-z0-9_-]-[A-Za-z0-9_-]*\z/' : '/^\z/', $id) || $id !== (string)$gid) { return 'E_SCOPE'; }
    if ($n && strpos($p[3], $id[0]) === false) { return 'E_KIND'; }
    $h = $mode === 1 ? 7 + $n : 80 + $n;
    if ($len < $h + 12 + 17) { return 'E_SHORT'; }
    if ($mode !== 1) {
        if (ord($env[15 + $n]) !== 4) { return 'E_POINT'; }
        if ($kid !== null) {
            $k = (strlen((string)$kid) === 16 && ctype_xdigit((string)$kid)) ? hex2bin((string)$kid) : (string)$kid;
            if (substr($env, 7 + $n, 8) !== $k) { return 'E_KID'; }
        }
    }
    return '';
}

// The check, then: the ciphertext must not look like readable text (from 64 bytes on, more than 85% printable bytes
// is E_TEXT), so these scripts can't be used to park readable files on the host.
function sealed_host_check($env, $purpose, $gid, $kid) {
    $e = sealed_check($env, $purpose, $gid, $kid);
    if ($e !== '') { return $e; }
    $n = ord($env[6]);
    $h = ord($env[4]) === 1 ? 7 + $n : 80 + $n;
    $c = (string)substr($env, $h + 12);
    $cl = strlen($c);
    if ($cl >= 64 && preg_match_all('/[\x09\x0a\x0d\x20-\x7e]/', $c) * 100 > 85 * $cl) { return 'E_TEXT'; }
    return '';
}

function sealed_status($err) {
    return $err === 'E_LONG' ? 413 : 400;
}
