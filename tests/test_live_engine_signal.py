import signal

import live_engine


def test_handle_shutdown_signal_sets_the_shared_stop_event():
    live_engine._stop_event.clear()
    try:
        live_engine._handle_shutdown_signal(signal.SIGTERM, None)
        assert live_engine._stop_event.is_set()
    finally:
        live_engine._stop_event.clear()


def test_install_signal_handlers_registers_sigterm_and_sigint(monkeypatch):
    calls = []
    monkeypatch.setattr(signal, "signal", lambda sig, handler: calls.append((sig, handler)))
    live_engine.install_signal_handlers()
    assert calls == [
        (signal.SIGTERM, live_engine._handle_shutdown_signal),
        (signal.SIGINT, live_engine._handle_shutdown_signal),
    ]
