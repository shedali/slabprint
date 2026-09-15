# Security

## Reporting

Please report security issues by opening a GitHub security advisory on this
repository rather than a public issue.

## Scope, honestly

`slabprint serve` is a convenience for a home network, not a hardened service.
It has no TLS, and its shared token is a header compared with `hmac.compare_digest`
— enough to stop a casual mistake, not an attacker. It listens on `127.0.0.1`
unless you explicitly ask for more, and it prints whatever it is given.

Do not expose it to the internet. On a network you do not control, don't run it
at all.

Reports that amount to "the print service has weak authentication" are already
known and documented here. Reports of anything that escapes those bounds — a way
to read files, run commands, or reach beyond printing — are very much wanted.

## Hardware

This software talks to a physical printer over USB and Bluetooth. It sends only
the documented print and status commands in PROTOCOL.md; it never writes firmware
or serial numbers, and exposes no command that can do so.
