
import streamlit as st
from supabase import create_client, Client


def _new_client() -> Client:
    try:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_ANON_KEY"]
    except KeyError:
        st.error(
            "Missing SUPABASE_URL or SUPABASE_ANON_KEY "
            "in Streamlit Secrets."
        )
        st.stop()

    return create_client(url, key)


def get_client() -> Client:
    if "sb" not in st.session_state:
        st.session_state.sb = _new_client()

    return st.session_state.sb


def current_user():
    return st.session_state.get("user")


def _store(res) -> None:
    if not res.session or not res.user:
        raise Exception("Authentication did not return a valid session.")

    st.session_state.user = {
        "id": res.user.id,
        "email": res.user.email,
    }

    # Apply Supabase user's JWT to database requests.
    get_client().postgrest.auth(res.session.access_token)


def sign_up(email: str, password: str) -> str:
    sb = get_client()

    res = sb.auth.sign_up({
        "email": email,
        "password": password,
    })

    if res.session:
        _store(res)
        return "Account created. You are logged in."

    return (
        "Account created. Check your email and click "
        "the confirmation link, then log in."
    )


def sign_in(email: str, password: str) -> None:
    sb = get_client()

    res = sb.auth.sign_in_with_password({
        "email": email,
        "password": password,
    })

    _store(res)


def reset_password(email: str) -> None:
    get_client().auth.reset_password_email(email)


def sign_out() -> None:
    try:
        get_client().auth.sign_out()
    except Exception:
        pass

    for key in ("sb", "user"):
        st.session_state.pop(key, None)

    # Also clear Streamlit's Google login cookie.
    if getattr(st.user, "is_logged_in", False):
        st.logout()


def handle_auth_callback():
    # Streamlit handles the Google OIDC callback automatically.
    pass


def require_login() -> dict:
    user = current_user()

    # Exchange Google's ID token for a Supabase session.
    if not user and st.user.is_logged_in:
        try:
            id_token = st.user.tokens["id"]

            res = get_client().auth.sign_in_with_id_token({
                "provider": "google",
                "token": id_token,
            })

            _store(res)
            user = current_user()

        except Exception as e:
            st.error(f"Google authentication failed: {e}")
            st.stop()

    if user:
        return user

    st.title("🎬 Shorts Agent")
    st.caption(
        "Log in or create an account to set up "
        "your own automated Shorts channel."
    )

    st.subheader("Sign in with Google")

    if st.button(
        "🔵 Sign in with Google",
        use_container_width=True,
    ):
        st.login("google")

    st.divider()
    st.caption("Or use your email and password")

    tab_login, tab_signup = st.tabs(["Log in", "Sign up"])

    with tab_login:
        with st.form("login_form"):
            email = st.text_input("Email")
            password = st.text_input(
                "Password",
                type="password",
            )
            go = st.form_submit_button(
                "Log in",
                use_container_width=True,
            )

        if go:
            try:
                sign_in(email.strip(), password)
                st.rerun()
            except Exception as e:
                st.error(f"Login failed: {e}")

        with st.expander("Forgot password?"):
            fe = st.text_input(
                "Your email",
                key="forgot_email",
            )

            if st.button("Send reset link"):
                try:
                    reset_password(fe.strip())
                    st.success(
                        "If that email exists, a reset link was sent."
                    )
                except Exception as e:
                    st.error(str(e))

    with tab_signup:
        with st.form("signup_form"):
            email = st.text_input(
                "Email",
                key="su_email",
            )
            password = st.text_input(
                "Password (min 8 characters)",
                type="password",
                key="su_pw",
            )
            password2 = st.text_input(
                "Confirm password",
                type="password",
                key="su_pw2",
            )

            go = st.form_submit_button(
                "Create account",
                use_container_width=True,
            )

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
