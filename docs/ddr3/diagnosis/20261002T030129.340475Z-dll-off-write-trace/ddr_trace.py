"""Observational, diagnostic-only recorder for one DDR3 physical burst."""

from migen import Array, Cat, Constant, If, Signal
from litex.gen import LiteXModule
from litex.soc.interconnect.csr import CSR, CSRStatus, CSRStorage
from gateware.ddr_geometry import DDR_SIZE_BYTES, DDR_ROW_BITS, DDR_PART


FRAME_FIELDS = {
    "write_data": (0, 128), "write_mask": (128, 16),
    "serializer_data": (144, 64), "serializer_mask": (208, 8),
    "dq_tx": (216, 2), "dqs_tx": (218, 2),
    "write_enable": (220, 2), "read_enable": (222, 2),
    "read_valid": (224, 2), "command_address": (226, 14),
    "command_bank": (240, 3), "command_signals": (243, 4),
}


def burst_address(byte_offset):
    """Pinned x16 BL8, 10-column/3-bank ROW_BANK_COL address mapping."""
    if not 0 <= byte_offset < DDR_SIZE_BYTES or byte_offset & 3:
        raise ValueError("DDR offset must be a word-aligned address in the fitted 128 MiB")
    native = byte_offset >> 4
    return {"row": native >> 10, "bank": (native >> 7) & 7,
            "column": (native & 127) << 3, "word_lane": (byte_offset >> 2) & 3}


def decode_frame(value):
    return {name: (value >> start) & ((1 << width) - 1)
            for name, (start, width) in FRAME_FIELDS.items()}


class DDRBurstTrace(LiteXModule):
    """Capture eight PCLK frames and three adjacent returned-data samples.

    No recorder signal feeds the controller or PHY. Row tracking observes both
    command phases, including precharge/refresh/auto-precharge. Each arm records
    the first matching write, then the first matching read after that capture.
    Serializer fields observe OSER4_MEM input flops, not external pin waveforms.
    """

    def __init__(self, dfi, serializer_data, serializer_mask, dq_tx, dqs_tx,
                 read_latency, target_offset=0xb64f4):
        if len(dfi.phases) != 2 or len(dfi.phases[0].wrdata) != 64:
            raise ValueError("trace requires the pinned two-phase x16 BL8 PHY")
        if read_latency < 2:
            raise ValueError("read latency must permit early/nominal/late samples")
        target = burst_address(target_offset)
        self.bank = CSRStorage(3, reset=target['bank'], name='bank')
        self.row = CSRStorage(14, reset=target['row'], name='row')
        self.column = CSRStorage(10, reset=target['column'], name='column')
        self.arm = CSR(name='arm')
        self.status = CSRStatus(8, name='status')
        self.hit_cycle = CSRStatus(32, name='hit_cycle')
        self.index = CSRStorage(3, name='index')
        self.frame = CSRStatus(256, name='frame')
        self.read_early = CSRStatus(128, name='read_early')
        self.read_nominal = CSRStatus(128, name='read_nominal')
        self.read_late = CSRStatus(128, name='read_late')
        self.read_valid = CSRStatus(3, name='read_valid')

        rows = Array(Signal(14) for _ in range(8))
        opened = Signal(8)
        write_match = Signal()
        read_match = Signal()
        writes, reads = [], []
        for phase in dfi.phases:
            selected = ~phase.cs_n[0]
            activate = selected & ~phase.ras_n & phase.cas_n & phase.we_n
            precharge = selected & ~phase.ras_n & phase.cas_n & ~phase.we_n
            refresh = selected & ~phase.ras_n & ~phase.cas_n & phase.we_n
            access = selected & phase.ras_n & ~phase.cas_n
            row_match = Array(opened[bank] for bank in range(8))[phase.bank] & (
                rows[phase.bank] == self.row.storage)
            target_match = row_match & (phase.bank == self.bank.storage) & (
                phase.address[:10] == self.column.storage)
            writes.append(access & ~phase.we_n & target_match)
            reads.append(access & phase.we_n & target_match)
            for bank in range(8):
                self.sync += If(activate & (phase.bank == bank),
                    rows[bank].eq(phase.address[:14]), opened[bank].eq(1))
                self.sync += If(refresh | (precharge & (phase.address[10] | (phase.bank == bank)))
                    | (access & phase.address[10] & (phase.bank == bank)), opened[bank].eq(0))
        self.comb += [write_match.eq(writes[0] | writes[1]),
                      read_match.eq(reads[0] | reads[1])]

        phase = dfi.phases[0]
        packed = Cat(*(p.wrdata for p in dfi.phases),
                     *(p.wrdata_mask for p in dfi.phases),
                     serializer_data, serializer_mask, dq_tx, dqs_tx,
                     Cat(*(p.wrdata_en for p in dfi.phases)),
                     Cat(*(p.rddata_en for p in dfi.phases)),
                     Cat(*(p.rddata_valid for p in dfi.phases)),
                     phase.address, phase.bank,
                     Cat(phase.cs_n[0], phase.ras_n, phase.cas_n, phase.we_n), Constant(0, 9))
        if len(packed) != 256:
            raise ValueError("unexpected trace frame width")
        frames = Array(Signal(256, reset_less=True) for _ in range(8))
        self.comb += self.frame.status.eq(frames[self.index.storage])
        cycle = Signal(32)
        active = Signal()
        slot = Signal(3)
        write_armed = Signal(reset=1)
        read_armed = Signal(reset=1)
        write_done = Signal()
        read_done = Signal()
        pipe = Signal(read_latency + 1)
        self.comb += self.status.status.eq(Cat(write_done, read_done, active,
                                               write_armed, read_armed))
        self.sync += cycle.eq(cycle + 1)
        self.sync += If(self.arm.wr_stb,
            write_armed.eq(1), read_armed.eq(1), write_done.eq(0), read_done.eq(0),
            active.eq(0), slot.eq(0), pipe.eq(0), self.read_valid.status.eq(0)
        ).Else(
            If(write_match & write_armed,
                self.hit_cycle.status.eq(cycle), frames[0].eq(packed),
                write_armed.eq(0), active.eq(1), slot.eq(1)
            ).Elif(active,
                frames[slot].eq(packed), slot.eq(slot + 1),
                If(slot == 7, active.eq(0), write_done.eq(1))
            ),
            pipe.eq(Cat(read_match & read_armed & write_done, pipe[:-1])),
            If(read_match & read_armed & write_done, read_armed.eq(0)),
            If(pipe[read_latency - 2],
                self.read_early.status.eq(Cat(*(p.rddata for p in dfi.phases))),
                self.read_valid.status[0].eq(phase.rddata_valid)),
            If(pipe[read_latency - 1],
                self.read_nominal.status.eq(Cat(*(p.rddata for p in dfi.phases))),
                self.read_valid.status[1].eq(phase.rddata_valid)),
            If(pipe[read_latency],
                self.read_late.status.eq(Cat(*(p.rddata for p in dfi.phases))),
                self.read_valid.status[2].eq(phase.rddata_valid), read_done.eq(1))
        )


def add_burst_trace(soc):
    from migen import Instance

    def pin(instance, name):
        return next(item.expr for item in instance.items
                    if isinstance(item, Instance.Input) and item.name == name)

    serials = sorted((item for item in soc.ddrphy._fragment.specials
                      if isinstance(item, Instance) and item.of == 'OSER4_MEM'), key=lambda x: x.duid)
    if len(serials) != 20:
        raise ValueError("expected DQS/DM/eight DQ serializers for each of two lanes")
    dqs = [serials[0], serials[10]]
    dm = [serials[1], serials[11]]
    dq = serials[2:10] + serials[12:20]
    data = Cat(*(pin(instance, f'D{edge}') for edge in range(4) for instance in dq))
    mask = Cat(*(pin(instance, f'D{edge}') for edge in range(4) for instance in dm))
    soc.ddr_trace = DDRBurstTrace(soc.ddrphy.dfi, data, mask,
        Cat(pin(dq[0], 'TX0'), pin(dq[0], 'TX1')),
        Cat(pin(dqs[0], 'TX0'), pin(dqs[0], 'TX1')),
        soc.ddrphy.settings.read_latency)
    return {'target_offset': '0x000b64f4', **burst_address(0xb64f4), 'frames': 8,
            'frame_bits': 256, 'frame_fields': FRAME_FIELDS,
            'read_latency': soc.ddrphy.settings.read_latency,
            'observation': 'DFI and OSER4_MEM PCLK inputs; no external pin waveform',
            'memory_part': DDR_PART, 'geometry_bytes': DDR_SIZE_BYTES,
            'controller_mapping': f'ROW_BANK_COL, x16 BL8, 10 column / 3 bank / {DDR_ROW_BITS} row bits'}
