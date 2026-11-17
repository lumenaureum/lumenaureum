<?php
require __DIR__ . '/../plugins/sealed_assets/sealed.php';

$V = json_decode((string)file_get_contents($argv[1]), true);
$K = $V['keys'];
$pass = 0;
$fail = [];
function ok($cond, $name) {
    global $pass, $fail;
    if ($cond) { $pass++; } else { $fail[] = $name; }
}

$purposes = [];
foreach (SEALED_PURPOSES as $k => $p) { $purposes[$k] = ['code' => $p[0], 'mode' => $p[1], 'max' => $p[2], 'kinds' => $p[3]]; }
ok($purposes == $V['purposes'] && array_keys($purposes) === array_keys($V['purposes']), 'purposes');

foreach ($V['valid'] as $v) {
    if (!isset($v['envelope_hex'])) { continue; }
    $env = hex2bin($v['envelope_hex']);
    $kid = $V['purposes'][$v['purpose']]['mode'] === 1 ? null : $K['kid_hex'];
    ok(sealed_check($env, $v['purpose'], $v['gid'], $kid) === '', $v['name'] . ':check');
    ok(sealed_host_check($env, $v['purpose'], $v['gid'], $kid) === '', $v['name'] . ':host');
    ok(sealed_host_check($env, $v['purpose'], $v['gid'], $kid === null ? null : hex2bin($kid)) === '', $v['name'] . ':host-raw-kid');
    $d = sealed_decode($v['envelope_b64u'], $v['purpose']);
    ok($d[1] === '' && $d[0] === $env, $v['name'] . ':decode');
}
foreach ($V['invalid'] as $v) {
    if (isset($v['envelope_rule'])) {
        $r = $v['envelope_rule'];
        $pre = hex2bin($r['prefix_hex']);
        $env = $pre . str_repeat(hex2bin($r['fill_byte']), $r['total_len'] - strlen($pre));
    } else {
        $env = hex2bin($v['envelope_hex']);
    }
    $x = $v['expect'];
    ok(sealed_check($env, $x['purpose'], $x['gid'], $x['kid_hex']) === $v['check'], $v['name'] . ':check');
    ok(sealed_host_check($env, $x['purpose'], $x['gid'], $x['kid_hex']) === $v['host'], $v['name'] . ':host');
    ok(sealed_status($v['host'] === '' ? 'E_AUTH' : $v['host']) === ($v['host'] === 'E_LONG' ? 413 : 400), $v['name'] . ':status');
}
foreach ($V['transport'] as $t) {
    $d = isset($t['d_rule']) ? str_repeat($t['d_rule']['char'], $t['d_rule']['len']) : $t['d'];
    $got = sealed_decode($d, $t['purpose']);
    $same = $got[1] === $t['error'];
    if ($same && $t['error'] === '') {
        $same = isset($t['bytes_hex']) ? bin2hex($got[0]) === $t['bytes_hex']
              : strlen($got[0]) === $t['bytes_len'] && hash('sha256', $got[0]) === $t['bytes_sha256'];
    }
    ok($same, $t['name']);
}
ok(sealed_decode('AAAA', 'nonsense')[1] === 'E_PURPOSE' && sealed_check('LAS1', 'nonsense', '', null) === 'E_PURPOSE', 'unknown-purpose');
echo json_encode(['pass' => $pass, 'fail' => $fail, 'purposes' => $purposes, 'php' => PHP_VERSION]), "\n";

