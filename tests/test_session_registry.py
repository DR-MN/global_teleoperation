"""Session registry tests — rendezvous + TTL pruning."""
import time

from teleop.cloud import SessionRegistry, Role


def test_join_and_find_peer():
    r = SessionRegistry()
    r.join("s1", "leader-1", Role.LEADER)
    r.join("s1", "follower-1", Role.FOLLOWER)
    peers = r.peers("s1", exclude="leader-1")
    assert len(peers) == 1
    assert peers[0].peer_id == "follower-1"
    assert peers[0].role == Role.FOLLOWER


def test_leave_removes_empty_session():
    r = SessionRegistry()
    r.join("s1", "a", Role.VIEWER)
    r.leave("s1", "a")
    assert r.get("s1") is None


def test_role_count():
    r = SessionRegistry()
    r.join("s1", "l", Role.LEADER)
    r.join("s1", "f", Role.FOLLOWER)
    r.join("s1", "v", Role.VIEWER)
    sess = r.get("s1")
    assert sess.role_count(Role.LEADER) == 1
    assert sess.role_count(Role.VIEWER) == 1


def test_prune_drops_stale_peers():
    r = SessionRegistry(peer_ttl_s=0.05)
    r.join("s1", "a", Role.LEADER)
    time.sleep(0.06)
    assert r.prune() == 1
    assert r.get("s1") is None


def test_heartbeat_keeps_peer_alive():
    r = SessionRegistry(peer_ttl_s=0.1)
    r.join("s1", "a", Role.LEADER)
    time.sleep(0.06)
    r.heartbeat("s1", "a")
    time.sleep(0.06)
    assert r.prune() == 0   # heartbeat refreshed last_seen
    assert r.get("s1") is not None
