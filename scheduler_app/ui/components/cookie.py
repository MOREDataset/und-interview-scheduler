"""Tiny component that sets or clears the 'remember me' cookie in the browser."""
from __future__ import annotations

import streamlit as st

COOKIE = "und_sched_session"

_JS = r"""
export default function (component) {
  const { data } = component;
  if (!data || data.done) return;
  const secure = location.protocol === 'https:' ? '; Secure' : '';
  if (data.value) {
    document.cookie = `${data.name}=${data.value}; Max-Age=${data.maxAge}; Path=/; SameSite=Strict${secure}`;
  } else {
    document.cookie = `${data.name}=; Max-Age=0; Path=/; SameSite=Strict${secure}`;
  }
}
"""

_component = st.components.v2.component("session_cookie", js=_JS)


def set_cookie(value: str | None, max_age: int = 12 * 3600) -> None:
    _component(data={"name": COOKIE, "value": value or "", "maxAge": max_age}, key=f"cookie_{bool(value)}", height=0)
