# Peripheral testing checkpoint

Updated October 6, 2026, approximately 13:30 UTC. Implementation is partial pending physical acceptance; continue the active hardware batch before another programming operation.

- User confirmed core v3961 / Dock v3714, no unique printed serial, LAN with DHCP, installed 32 GB exFAT card, and requested exFAT support. The user cannot reseat the card now. Do not repeat those questions.
- Current suite: 123 tests pass. All 24 fitted builds have current firmware and exact image hashes. Six resource/placement failures are being rechecked using final source.
- All 15 on-chip reduced diagnostics passed controller/UART execution with hash-verified recovery. These have no filesystem/network services and are not full acceptance.
- Standard SD-only DDR qualification passed ten fresh training runs, full 128 MiB patterns/cache checks and 1800.051835 measured stress seconds. Minimal Ethernet-only DDR qualification passed the same with 1800.54455225 seconds. Reuse their exact unchanged images for firmware-only updates.
- SD initialization is blocked: CMD0 returns ten ff bytes despite bounded retries. No physical test-file writes occurred. Physical card reset/reseating and removal/reinsertion acceptance remain pending.
- Ethernet identifies RTL8201F at PHY address 0, ID 001c:c816. Page 7 register 16 is 0ffa before/after checking RMII and PHY clock output; BMCR 1000 and BMSR 7849 show negotiation enabled but no link. Restart did not establish link. A physical cable/port/link-LED check was requested asynchronously. No DHCP/static/echo/combined pass is claimed.
- The DHCP error path now retains capture files through net stop; later sessions confirm shutdown before idle reconfiguration.
- The last completed application retry is evidence/20261006T132358.565230Z-app-minimal, followed by net stop and verified safe idle (5.00761058 s, zero UART bytes). The on-chip batch subsequently completed and recorded another recovery.
- Active DDR batch: /tmp/qualify-remaining-ddr.py, exec session 68304. Log build/peripherals/remaining-ddr-qualification.log. It qualifies lite Ethernet, lite combined, minimal combined, minimal SD, lite SD, linux SD, performance SD, each with ten training runs and 1800 actual stress seconds. It excludes the two already qualified images and defers card/network application attempts pending prerequisites. It captures safe idle after each. Do not open UART/JTAG concurrently. Results build/peripherals/hardware-batch/remaining-ddr-results.json.
- Failed build final recheck: exec session 81681, build/peripherals/failed-build-final-recheck.log. No hardware ownership.
- No flash programming, formatting, partition changes, commits or pushes. Recovery SRAM hash 20c4f1f7be50e692b53371dba25c833522562db445c2b75e6c9cf2cf1d6d3784 holds DDR reset, SD deselected/clock stopped, PHY reset and TX disabled.

After the batch, validate all exact-image evidence, regenerate scripts/peripheral_report.py, update hardware observations/worklog/checkpoint with actual outcomes and final recovery. Resume physical card/LAN acceptance when the prerequisites become available. Failed or missing acceptance stays incomplete.
