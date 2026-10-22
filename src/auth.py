"""Multi-user authentication on top of Supabase Auth.

Important: the authenticated client lives in st.session_state (one per browser
session). Never put it in st.cache_resource, or users would share a login.
"""
import streamlit as st
from supabase import create_client, Client


def _new_client() -> Client:
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_ANON_KEY"])


def get_client() -> Client:
    if "sb" not in st.session_state:
        st.session_state.sb = _new_client()
    return st.session_state.sb


def current_user():
    return st.session_state.get("user")


def sign_up(email: str, password: str) -> str:
    """Returns a message to show the user."""
    sb = get_client()
    res = sb.auth.sign_up({"email": email, "password": password})
    if res.session:                      # email confirmation disabled -> logged in at once
        _store(res)
        return "Account created. You are logged in."
    return "Account created. Check your email and click the confirmation link, then log in."


def sign_in(email: str, password: str) -> None:
    sb = get_client()
    res = sb.auth.sign_in_with_password({"email": email, "password": password})
    _store(res)


def reset_password(email: str) -> None:
    get_client().auth.reset_password_email(email)


def sign_out() -> None:
    try:
        get_client().auth.sign_out()
    except Exception:
        pass
    for k in ("sb", "user"):
        st.session_state.pop(k, None)


def _store(res) -> None:
    st.session_state.user = {"id": res.user.id, "email": res.user.email}
    # make the DB client send this user's JWT so Row Level Security applies
    get_client().postgrest.auth(res.session.access_token)


def require_login() -> dict:
    """Show the login/sign-up screen and stop the page until the user is logged in."""
    user = current_user()
    if user:
        return user

    st.title("🎬 Shorts Agent")
    st.caption("Log in or create an account to set up your own automated Shorts channel.")
    tab_login, tab_signup = st.tabs(["Log in", "Sign up"])

    with tab_login:
        with st.form("login_form"):
            email = st.text_input("Email")
            password = st.text_input("Password", type="password")
            go = st.form_submit_button("Log in", use_container_width=True)
        if go:
            try:
                sign_in(email.strip(), password)
                st.rerun()
            except Exception as e:
                st.error(f"Login failed: {e}")
        with st.expander("Forgot password?"):
            fe = st.text_input("Your email", key="forgot_email")
            if st.button("Send reset link"):
                try:
                    reset_password(fe.strip())
                    st.success("If that email exists, a reset link was sent.")
                except Exception as e:
                    st.error(str(e))

    with tab_signup:
        with st.form("signup_form"):
            email = st.text_input("Email", key="su_email")
            password = st.text_input("Password (min 8 characters)", type="password", key="su_pw")
            password2 = st.text_input("Confirm password", type="password", key="su_pw2")
            go = st.form_submit_button("Create account", use_container_width=True)
        if go:
            if len(password) < 8:
                st.error("Password must be at least 8 characters.")
            elif password != password2:
                st.error("Passwords do not match.")
            else:
                try:
                    msg = sign_up(email.strip(), password)
                    st.success(msg)
                    if current_user():
                        st.rerun()
                except Exception as e:
                    st.error(f"Sign-up failed: {e}")

    st.stop()
