"""Server-side teacher account allowlist.

Keep password values out of this file. Generate a Werkzeug password hash with:
python -c "from werkzeug.security import generate_password_hash; print(generate_password_hash('your-password'))"
"""

TEACHER_ACCOUNTS = [
    {
        "email": "2353877811@qq.com",
        "password_hash": "scrypt:32768:8:1$LsiLllEtFmAn532n$28aeb0058ab236df4975eabd52d23cc2e11d5639c7c0f10bb80ffe82d47fcce2e16bb52340f1040c18cf065ca04a8a40710637f358e40674d96f92ceb13b4834",
    },
    {
        "email": "admin@example.com",
        "password_hash": "scrypt:32768:8:1$tN0oT9bEP0Iw2Lub$50f245d3a01428fe02f78a05addeb56898882f176c4c3327c0600ee5ef347e6a5971ad636a5e887b855e38e4df5002128f0d14308283305834a820577037b275",
    },
]
