"""Password boxes are typed until they keep the entry (f330).

Analog Devices (Workday), 3 November: two of four runs stopped with the email
filled and both password boxes empty -- the single type-and-read mistook a
box that had not registered the keystrokes yet for a refusal. Each box is now
retyped from cleared until a read-back shows it kept, and a box that never
keeps names itself in the stop reason.
"""
from browser_automation import _type_password_box_until_kept


class FakeBox:
    def __init__(self, clears=0):
        self.clears = clears
        self._value = ""
        self.types = 0

    def click(self):
        pass

    def press(self, key):
        if key == "Backspace":
            self._value = ""

    def press_sequentially(self, text, delay=25):
        self._value = text
        self.types += 1

    def input_value(self):
        if self.clears > 0:
            self.clears -= 1
            self._value = ""
            return ""
        return self._value


class FakePage:
    def wait_for_timeout(self, _ms):
        pass


def test_a_steady_box_is_typed_once():
    assert _type_password_box_until_kept(FakePage(), FakeBox(), "pw") is True


def test_a_box_that_drops_keystrokes_is_retyped_until_it_keeps():
    box = FakeBox(clears=2)
    assert _type_password_box_until_kept(FakePage(), box, "pw") is True
    assert box.types == 3 and box.input_value() == "pw"


def test_a_box_that_never_keeps_gives_up_after_three_tries():
    box = FakeBox(clears=99)
    assert _type_password_box_until_kept(FakePage(), box, "pw") is False
    assert box.types == 3
