# Contributing

Bug reports, protocol findings and patches are all welcome.

## Licensing of contributions

This project is MIT licensed, and contributions are accepted on the same terms —
inbound equals outbound. By opening a pull request you confirm that:

- you wrote the contribution, or otherwise have the right to submit it; and
- you licence it to this project and its users under the MIT Licence.

A `Signed-off-by` line ([Developer Certificate of Origin](https://developercertificate.org/))
is welcome but not required.

## What cannot be accepted

To keep this project cleanly licensed, please do **not** submit:

- vendor code, decompiler output, or text copied from vendor documentation;
- binaries, firmware, or fonts belonging to anyone else;
- anything you are under an obligation of confidence about.

Protocol findings are welcome, but contribute them the way PROTOCOL.md is
written: as your own description of observed behaviour — byte values, layouts,
what a device did in response to what — not as someone else's material.

## Before you open a pull request

```bash
./scripts/install-hooks.sh   # lint, format and tests run on every commit
```

The same three checks run in CI, and the nix build runs the test suite in its
sandbox. All of them are hard failures.

Tests must not require a printer. They do need pypdfium2, for the PDF path.
 The USB and Bluetooth transports are
deliberately untested rather than mocked, because a mock of a printer only
proves the mock works — the interesting failures there are physical.

## Reporting a protocol finding

Say which model you have, which transport, and what you observed. Firmware
versions differ, and only the MIP-001 has been verified against real hardware.
