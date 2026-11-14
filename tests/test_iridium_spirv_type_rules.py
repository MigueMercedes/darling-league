"""Translate a synthetic AIR kernel with the patched Iridium and check SPIR-V type rules.

The pinned Indium revision is exported from the local component checkout, the shipped patch
is applied, and the repo's offline translator is built natively against the host LLVM. No
game, client or guest process is involved. Skipped when the checkout or LLVM is missing.
"""
from pathlib import Path
import json
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import llvm_tools  # noqa: E402

COMPONENT = 'src/external/metal/deps/indium'
CHECKOUT = ROOT / 'build/darling-src' / COMPONENT
FIXTURE = ROOT / 'tests/fixtures/iridium-spirv-type-rules.ll'
TYPE_DECLARATIONS = {19, 20, 21, 22, 23, 24, 28, 29, 30, 32, 33}
TYPED_RESULTS = {41, 42, 43, 44, 46, 59, 61, 65, 77, 78, 79, 80, 81, 82, 83}
INTEGER_BINOPS = {128, 130, 132}  # OpIAdd, OpISub, OpIMul


def pinned_revision():
    manifest = json.loads((ROOT / 'upstream/manifest.json').read_text())
    entries = [entry for entry in manifest['components'] if entry['path'] == COMPONENT]
    return entries[0]['revision']


def metallib(bitcode, name):
    """The minimal one-kernel metallib layout Iridium's AIR::Library reads."""
    def tag(label, payload):
        return label + struct.pack('<H', len(payload)) + payload
    group = (tag(b'NAME', name.encode() + b'\0') + tag(b'TYPE', bytes([2])) +
             tag(b'MDSZ', struct.pack('<Q', len(bitcode))) +
             tag(b'OFFT', struct.pack('<QQQ', 0, 0, 0)) + b'ENDT')
    functions = struct.pack('<II', 1, len(group) + 4) + group
    bitcode_offset = 88 + len(functions)
    header = (b'MTLB' + struct.pack('<HHHBBHH', 0x8001, 1, 2, 0, 0x81, 14, 0) +
              struct.pack('<QQQQQQQQQ', bitcode_offset + len(bitcode), 88, len(functions),
                          0, 0, 0, 0, bitcode_offset, len(bitcode)))
    return header + functions + bitcode


def type_rule_violations(module):
    """Violations of the SPIR-V rules the translator must respect, as readable strings."""
    words = struct.unpack(f'<{len(module) // 4}I', module)
    assert words[0] == 0x07230203, 'not a SPIR-V module'
    types, value_type, constants, instructions = {}, {}, {}, []
    index = 5
    while index < len(words):
        opcode, count = words[index] & 0xffff, words[index] >> 16
        operands = words[index + 1:index + count]
        instructions.append((opcode, operands))
        if opcode in TYPE_DECLARATIONS:
            types[operands[0]] = (opcode, operands[1:])
        elif opcode in TYPED_RESULTS or 109 <= opcode < 240:
            value_type[operands[1]] = operands[0]
            if opcode == 43:
                constants[operands[1]] = operands[2]
        index += count

    def is_boolean(type_id):
        opcode, rest = types[type_id]
        return opcode == 20 or (opcode == 23 and types[rest[0]][0] == 20)

    def describe(type_id):
        opcode, rest = types[type_id]
        if opcode == 20:
            return 'bool'
        if opcode == 21:
            return ('i' if rest[1] else 'u') + str(rest[0])
        if opcode == 23:
            return f'<{rest[1]} x {describe(rest[0])}>'
        if opcode == 28:
            return f'[{constants.get(rest[1], "?")} x {describe(rest[0])}]#{type_id}'
        if opcode == 30:
            return '{' + ', '.join(describe(member) for member in rest) + f'}}#{type_id}'
        return f'type{opcode}#{type_id}'

    violations, seen = [], set()
    for opcode, operands in instructions:
        seen.add(opcode)
        if opcode == 44:  # OpConstantComposite
            kind, rest = types[operands[0]]
            members = rest if kind == 30 else [rest[0]] * (len(operands) - 2)
            for member, constituent in zip(members, operands[2:]):
                if value_type[constituent] != member:
                    violations.append(f'{describe(operands[0])} constant holds '
                                      f'{describe(value_type[constituent])}')
        elif opcode in INTEGER_BINOPS:
            for operand in operands[2:]:
                if value_type[operand] != operands[0]:
                    violations.append(f'integer op on {describe(operands[0])} takes '
                                      f'{describe(value_type[operand])}')
        elif opcode in (170, 171) and is_boolean(value_type[operands[2]]):
            violations.append(f'OpIEqual/OpINotEqual on {describe(value_type[operands[2]])}')
        elif opcode in (113, 114) and is_boolean(value_type[operands[2]]):
            violations.append(f'integer conversion of {describe(value_type[operands[2]])}')
        elif opcode == 65:  # OpAccessChain
            selected = types[value_type[operands[2]]][1][1]
            for chain_index in operands[3:]:
                kind, rest = types[selected]
                selected = rest[constants[chain_index]] if kind == 30 else rest[0]
            declared = types[operands[0]][1][1]
            if declared != selected:
                violations.append(f'OpAccessChain declares {describe(declared)} '
                                  f'but selects {describe(selected)}')
    for opcode, purpose in ((164, 'OpLogicalEqual'), (165, 'OpLogicalNotEqual'), (169, 'OpSelect')):
        if opcode not in seen:
            violations.append(f'missing {purpose}')
    return violations


def prerequisites():
    if not (shutil.which('clang++') and shutil.which('git') and llvm_tools.find('llvm-config')):
        return 'clang++, git and llvm-config are required'
    if not (CHECKOUT / '.git').exists():
        return 'the Indium component checkout is not prepared'
    found = subprocess.run(['git', '-C', str(CHECKOUT), 'cat-file', '-e',
                            pinned_revision() + '^{commit}'], capture_output=True)
    return None if found.returncode == 0 else 'the pinned Indium revision is not in the checkout'


SKIP = prerequisites()


@unittest.skipIf(SKIP, SKIP)
class IridiumSpirvTypeRulesTest(unittest.TestCase):
    def test_translated_kernel_respects_spirv_type_rules(self):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            tree = work / 'indium'
            tree.mkdir()
            archive = subprocess.run(['git', '-C', str(CHECKOUT), 'archive', pinned_revision()],
                                     capture_output=True, check=True).stdout
            subprocess.run(['tar', '-x', '-C', str(tree)], input=archive, check=True)
            subprocess.run(['git', 'apply', str(ROOT / 'patches/metal--deps--indium.patch')],
                           cwd=tree, check=True)

            library = Path(llvm_tools.config('--libdir')) / llvm_tools.config('--link-shared',
                                                                               '--libnames')
            translator = work / 'translate'
            subprocess.run(['clang++', '-O0', '-std=c++17', '-w',
                            f'-DHOST_LLVM_LIBNAME="{library}"',
                            '-I' + llvm_tools.config('--includedir'),
                            '-I' + str(tree / 'include'), '-I' + str(tree / 'private-include'),
                            str(ROOT / 'tools/riot-air-translate-check.cpp'),
                            *[str(tree / 'src/iridium' / f'{name}.cpp')
                              for name in ('air', 'dynamic-llvm', 'iridium', 'spirv')],
                            '-ldl', '-o', str(translator)], check=True)

            assembler = Path(llvm_tools.config('--bindir')) / 'llvm-as'
            bitcode = work / 'probe.bc'
            subprocess.run([str(assembler), str(FIXTURE), '-o', str(bitcode)], check=True)
            (work / 'in').mkdir()
            (work / 'out').mkdir()
            (work / 'in/probe.bin').write_bytes(metallib(bitcode.read_bytes(), 'probe'))
            run = subprocess.run([str(translator), str(work / 'in'), str(work / 'out')],
                                 capture_output=True, text=True, timeout=60)
            self.assertIn('1 passed, 0 failed', run.stdout, run.stdout + run.stderr)

            module = (work / 'out/probe.bin.spv').read_bytes()
            self.assertEqual(type_rule_violations(module), [])


if __name__ == '__main__':
    unittest.main()
