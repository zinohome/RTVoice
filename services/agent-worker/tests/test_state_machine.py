"""StateMachine 单元测试。"""

from app.state_machine import State, StateMachine


def test_initial_state():
    fsm = StateMachine()
    assert fsm.state == State.IDLE


def test_legal_transitions():
    fsm = StateMachine()
    assert fsm.transition(State.LISTENING) is True
    assert fsm.state == State.LISTENING
    assert fsm.transition(State.THINKING) is True
    assert fsm.transition(State.SPEAKING) is True
    assert fsm.transition(State.IDLE) is True


def test_barge_in_path():
    fsm = StateMachine()
    fsm.transition(State.LISTENING)
    fsm.transition(State.THINKING)
    fsm.transition(State.SPEAKING)
    assert fsm.transition(State.INTERRUPTED) is True
    assert fsm.transition(State.LISTENING) is True


def test_illegal_transition_returns_false():
    fsm = StateMachine()
    # IDLE → SPEAKING 是非法转移
    assert fsm.transition(State.SPEAKING) is False
    assert fsm.state == State.IDLE


def test_force_overrides():
    fsm = StateMachine()
    fsm.transition(State.LISTENING)
    fsm.transition(State.THINKING)
    fsm.force(State.IDLE)   # 跳过中间合法转移
    assert fsm.state == State.IDLE


def test_on_change_callback():
    events = []
    fsm = StateMachine(on_change=lambda p, t: events.append((p, t)))
    fsm.transition(State.LISTENING)
    fsm.transition(State.THINKING)
    assert events == [
        (State.IDLE, State.LISTENING),
        (State.LISTENING, State.THINKING),
    ]


def test_callback_exception_does_not_break():
    def boom(p, t):
        raise RuntimeError("test")
    fsm = StateMachine(on_change=boom)
    fsm.transition(State.LISTENING)   # 不抛
    assert fsm.state == State.LISTENING
