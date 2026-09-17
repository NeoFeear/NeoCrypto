import signal

import pytest

import live_engine


def test_raise_keyboard_interrupt_raises():
    with pytest.raises(KeyboardInterrupt):
        live_engine._raise_keyboard_interrupt(signal.SIGTERM, None)


def test_install_signal_handlers_registers_sigterm(monkeypatch):
    calls = []
    monkeypatch.setattr(signal, "signal", lambda sig, handler: calls.append((sig, handler)))
    live_engine.install_signal_handlers()
    assert calls == [(signal.SIGTERM, live_engine._raise_keyboard_interrupt)]
