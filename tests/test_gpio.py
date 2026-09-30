import unittest

from migen import Module, Signal
from migen.sim import run_simulation

from litex.soc.cores.gpio import GPIOIn, GPIOOut


class GPIOProbe(Module):
    def __init__(self):
        self.led_resources = Signal(6, reset=0)
        self.button_pads_n = Signal(4, reset=0x0f)
        led_logical = Signal(6)
        button_pressed = Signal(4)
        self.submodules.leds = self.leds = GPIOOut(pads=led_logical, reset=0)
        self.comb += button_pressed.eq(~self.button_pads_n)
        for logical_bit, resource in enumerate(reversed(range(6))):
            self.comb += self.led_resources[resource].eq(~led_logical[logical_bit])
        self.submodules.buttons = self.buttons = GPIOIn(pads=button_pressed)


class GPIOTests(unittest.TestCase):
    def test_active_polarity_order_width_and_synchronizer(self):
        dut = GPIOProbe()
        self.assertEqual(len(dut.leds.out.storage), 6)
        self.assertEqual(len(dut.buttons._in.status), 4)
        self.assertEqual(dut.leds.out.storage.reset.value, 0)
        self.assertTrue(any(
            type(special).__name__ == "MultiReg"
            for special in dut.buttons._fragment.specials
        ))

        def bench():
            for _ in range(4):
                yield
            self.assertEqual((yield dut.led_resources), 0x3f)  # off is physical high
            self.assertEqual((yield dut.buttons._in.status), 0)

            yield dut.leds.out.storage.eq(0x09)
            yield dut.button_pads_n.eq(0x06)  # buttons 0 and 3 pressed, active low
            for _ in range(2):
                yield
                self.assertEqual((yield dut.buttons._in.status), 0)
            yield
            self.assertEqual((yield dut.buttons._in.status), 0x09)
            for _ in range(2):
                yield
            # Logical bits 0 and 3 map to platform resources 5 and 2.
            self.assertEqual((yield dut.led_resources), 0x1b)
            self.assertEqual((yield dut.buttons._in.status), 0x09)

        run_simulation(dut, bench(), clocks={"sys": 10})


if __name__ == "__main__":
    unittest.main()
