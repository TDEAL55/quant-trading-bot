from __future__ import annotations

from order_lifecycle import track_order_lifecycle


class _Broker:
    def __init__(self):
        self._rows = [
            {
                "order_id": "oid-1",
                "client_order_id": "cid-1",
                "status": "accepted",
                "requested_quantity": 10.0,
                "filled_quantity": 0.0,
                "average_fill_price": 0.0,
            },
            {
                "order_id": "oid-1",
                "client_order_id": "cid-1",
                "status": "partially_filled",
                "requested_quantity": 10.0,
                "filled_quantity": 4.0,
                "average_fill_price": 100.0,
            },
            {
                "order_id": "oid-1",
                "client_order_id": "cid-1",
                "status": "filled",
                "requested_quantity": 10.0,
                "filled_quantity": 10.0,
                "average_fill_price": 100.1,
            },
        ]
        self._idx = 0

    def get_order_by_id(self, order_id):
        del order_id
        if self._idx < len(self._rows):
            row = self._rows[self._idx]
            self._idx += 1
            return dict(row)
        return dict(self._rows[-1])


def test_track_order_lifecycle_records_transitions_and_final_fill():
    broker = _Broker()
    initial = broker.get_order_by_id("oid-1")

    lifecycle = track_order_lifecycle(broker=broker, initial_order=initial, poll_seconds=0.01, max_wait_seconds=1.0)

    assert lifecycle["final_status"] == "filled"
    assert lifecycle["is_filled"] is True
    transitions = lifecycle["status_transitions"]
    assert [item["status"] for item in transitions] == ["accepted", "partially_filled", "filled"]


def test_accepted_order_is_not_counted_as_filled():
    class _AcceptedOnlyBroker:
        def get_order_by_id(self, order_id):
            del order_id
            return {
                "order_id": "oid-2",
                "client_order_id": "cid-2",
                "status": "accepted",
                "requested_quantity": 5.0,
                "filled_quantity": 0.0,
                "average_fill_price": 0.0,
            }

    broker = _AcceptedOnlyBroker()
    initial = broker.get_order_by_id("oid-2")
    lifecycle = track_order_lifecycle(broker=broker, initial_order=initial, poll_seconds=0.01, max_wait_seconds=0.02)

    assert lifecycle["final_status"] == "accepted"
    assert lifecycle["is_filled"] is False
    assert lifecycle["fill_time"] == ""
