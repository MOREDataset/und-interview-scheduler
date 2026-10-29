"""Per-request context shared by pages, plus a helper for safe database writes."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field

import streamlit as st
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from ..core import models as m
from ..core.config import Settings
from ..core.services import ConcurrencyError, ServiceError, ValidationError


@dataclass
class Ctx:
    s: Session
    settings: Settings
    user: m.User | None = None
    search: m.Search | None = None
    pages: dict = field(default_factory=dict)

    @property
    def actor(self) -> str:
        return self.user.email if self.user else "anonymous"


@contextmanager
def write(ctx: Ctx, success: str | None = None, rerun: bool = True):
    """Run a change in a transaction. On success: commit, toast, rerun.
    On a validation or concurrency problem: roll back and show a friendly message."""
    ok = False
    try:
        yield
        ctx.s.commit()
        ok = True
    except ValidationError as exc:
        ctx.s.rollback()
        st.error(str(exc), icon=":material/block:")
    except (ConcurrencyError, StaleDataError):
        ctx.s.rollback()
        st.error("Someone else changed this record at the same time. Nothing was overwritten — "
                 "the page has the latest version now; please make your change again.", icon=":material/sync_problem:")
    except ServiceError as exc:
        ctx.s.rollback()
        st.error(str(exc), icon=":material/error:")
    except Exception:
        ctx.s.rollback()
        raise
    if ok:
        if success:
            st.session_state["_flash"] = success
        if rerun:
            st.rerun()


def flash() -> None:
    msg = st.session_state.pop("_flash", None)
    if msg:
        st.toast(msg, icon=":material/check_circle:")
