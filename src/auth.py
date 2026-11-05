import streamlit as st
from supabase import create_client, Client
from urllib.parse import urlparse, parse_qs


def _new_client() -> Client:
    # Safely fetch secrets, preventing a hard crash if they are missing
    try:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_ANON_KEY"]
    except KeyError:
        st.error("🚨 Missing Secrets! Please add SUPABASE_URL and SUPABASE_ANON_KEY to your Streamlit Secrets.")
        st.stop()

    return create_client(url, key)


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
    if res.session:  # email confirmation disabled -> logged in at once
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


def handle_auth_callback():
    """Handle Supabase OAuth callback using PKCE authorization code."""

    if current_user():
        return

    query_params = st.query_params

    code = query_params.get("code")
    access_token = query_params.get("access_token")

    # No authentication response: continue normal application flow.
    if not code and not access_token:
        return

    try:
        sb = get_client()

        if code:
            # Google OAuth returns an authorization code.
            res = sb.auth.exchange_code_for_session({
                "auth_code": code
            })

        else:
            # Preserve support for the previous implicit-token callback.
            res = sb.auth.set_session(
                access_token=access_token,
                refresh_token=query_params.get("refresh_token", "")
            )

        if not res.session or not res.user:
            raise Exception("Supabase did not return a valid user session.")

        # Save user and apply JWT to database requests.
        _store(res)

        # Remove temporary OAuth parameters.
        st.query_params.clear()

        # Reload application with authenticated user.
        st.rerun()

    except Exception as e:
        st.error(f"Authentication failed: {e}")
def require_login() -> dict:
    """Show the login/sign-up screen (Google + Email/Password)."""
    user = current_user()
    if user:
        return user

    st.title("🎬 Shorts Agent")
    st.caption("Log in or create an account to set up your own automated Shorts channel.")


    st.subheader("Sign in with Google")

    try:
        auth_url = get_client().auth.sign_in_with_oauth({
            "provider": "google",
            "options": {
                "redirect_to": "https://usaamus.streamlit.app/",
                "scopes": "openid email profile"
            }
        })

        st.markdown(
            f"""
            <a href="{auth_url.url}" target="_top"
               style="
                   display:block;
                   background-color:#4285F4;
                   color:white;
                   padding:12px;
                   text-align:center;
                   text-decoration:none;
                   border-radius:6px;
                   font-weight:bold;
               ">
                Sign in with Google
            </a>
            """,
            unsafe_allow_html=True
        )

    except Exception as e:
        st.error(f"Error setting up Google Login: {e}")



    st.divider()
    st.caption("Or use your email and password")

    # --- EMAIL/PASSWORD SECTION ---
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