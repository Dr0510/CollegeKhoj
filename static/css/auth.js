/* ============================================================
   CollegeKhoj — Auth JavaScript (Login & Signup)
   Custom password-based auth (Clerk-free)
   ============================================================ */

'use strict';

(function () {
  // ── Theme Management ──
  const THEME_KEY = 'cf-theme';

  function getTheme() {
    return localStorage.getItem(THEME_KEY) || 'dark';
  }

  function setTheme(theme) {
    localStorage.setItem(THEME_KEY, theme);
    document.documentElement.setAttribute('data-theme', theme);
    document.documentElement.setAttribute('data-bs-theme', theme);
  }

  function toggleTheme() {
    const current = getTheme();
    setTheme(current === 'dark' ? 'light' : 'dark');
  }

  // Initialize theme
  setTheme(getTheme());

  // Expose toggle globally
  window.toggleTheme = toggleTheme;

  // ── Password Visibility Toggle ──
  document.addEventListener('click', function (e) {
    const toggle = e.target.closest('.password-toggle');
    if (!toggle) return;

    const input = toggle.closest('.input-wrapper').querySelector('.input-field');
    if (!input) return;

    const isPassword = input.type === 'password';
    input.type = isPassword ? 'text' : 'password';

    const icon = toggle.querySelector('i');
    if (icon) {
      icon.className = isPassword ? 'fas fa-eye-slash' : 'fas fa-eye';
    }

    toggle.setAttribute('aria-label', isPassword ? 'Hide password' : 'Show password');
  });

  // ── Password Strength Meter (Signup) ──
  function calculatePasswordStrength(password) {
    let score = 0;
    if (password.length >= 8) score += 1;
    if (password.length >= 12) score += 1;
    if (/[a-z]/.test(password) && /[A-Z]/.test(password)) score += 1;
    if (/\d/.test(password)) score += 1;
    if (/[^a-zA-Z0-9]/.test(password)) score += 1;
    return Math.min(score, 4);
  }

  function getStrengthLabel(score) {
    const labels = ['', 'Weak', 'Fair', 'Good', 'Strong'];
    return labels[score] || '';
  }

  function updateStrengthMeter(password) {
    const meter = document.getElementById('password-strength-meter');
    if (!meter) return;

    const score = calculatePasswordStrength(password);
    const segments = meter.querySelectorAll('.segment');
    const labels = meter.querySelectorAll('.strength-meter-labels span');

    segments.forEach(function (s) { s.className = 'segment'; });
    labels.forEach(function (l) { l.classList.remove('active'); });

    if (password.length === 0) return;

    for (let i = 0; i < score; i++) {
      if (segments[i]) {
        segments[i].classList.add('fill-' + (i + 1));
      }
    }

    const labelIndex = Math.max(0, score - 1);
    if (labels[labelIndex]) {
      labels[labelIndex].classList.add('active');
    }
  }

  document.addEventListener('input', function (e) {
    if (e.target.id === 'signup-password') {
      updateStrengthMeter(e.target.value);
    }
  });

  // ── Confirm Password Match ──
  function checkPasswordMatch() {
    const password = document.getElementById('signup-password');
    const confirm = document.getElementById('signup-confirm-password');
    const error = document.getElementById('confirm-password-error');

    if (!password || !confirm || !error) return;

    if (confirm.value.length > 0 && password.value !== confirm.value) {
      confirm.classList.add('error');
      error.querySelector('span').textContent = 'Passwords do not match';
      error.classList.add('visible');
      return false;
    } else {
      confirm.classList.remove('error');
      error.querySelector('span').textContent = '';
      error.classList.remove('visible');
      return true;
    }
  }

  document.addEventListener('input', function (e) {
    if (e.target.id === 'signup-password' || e.target.id === 'signup-confirm-password') {
      checkPasswordMatch();
    }
  });

  // ── Form Validation Helpers ──

  function isValidEmail(email) {
    return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
  }

  function showFieldError(fieldId, message) {
    const field = document.getElementById(fieldId);
    const errorEl = document.getElementById(fieldId + '-error');
    if (field) field.classList.add('error');
    if (errorEl) {
      errorEl.querySelector('span').textContent = message;
      errorEl.classList.add('visible');
    }
  }

  function clearFieldError(fieldId) {
    const field = document.getElementById(fieldId);
    const errorEl = document.getElementById(fieldId + '-error');
    if (field) field.classList.remove('error');
    if (errorEl) {
      errorEl.querySelector('span').textContent = '';
      errorEl.classList.remove('visible');
    }
  }

  function clearAllErrors() {
    document.querySelectorAll('.input-field.error').forEach(function (el) {
      el.classList.remove('error');
    });
    document.querySelectorAll('.field-error.visible').forEach(function (el) {
      el.classList.remove('visible');
      el.querySelector('span').textContent = '';
    });
  }

  // ── Toast Notifications ──
  function showToast(message, type) {
    type = type || 'info';
    const container = document.getElementById('toast-container');
    if (!container) return;

    const toast = document.createElement('div');
    toast.className = 'toast toast-' + type;
    toast.setAttribute('role', 'alert');
    toast.setAttribute('aria-live', 'polite');

    const iconMap = {
      success: 'fa-check-circle',
      error: 'fa-exclamation-circle',
      info: 'fa-info-circle',
    };

    toast.innerHTML =
      '<i class="fas ' +
      (iconMap[type] || 'fa-info-circle') +
      '"></i>' +
      '<span>' +
      escapeHtml(message) +
      '</span>' +
      '<button class="toast-close" onclick="this.parentElement.remove()" aria-label="Dismiss">&times;</button>';

    container.appendChild(toast);

    setTimeout(function () {
      if (toast.parentElement) {
        toast.style.opacity = '0';
        toast.style.transform = 'translateX(40px)';
        toast.style.transition = 'all 0.3s ease';
        setTimeout(function () {
          if (toast.parentElement) toast.remove();
        }, 300);
      }
    }, 5000);
  }

  function escapeHtml(text) {
    var div = document.createElement('div');
    div.appendChild(document.createTextNode(text));
    return div.innerHTML;
  }

  window.showToast = showToast;

  function showError(banner, message) {
    if (banner) {
      banner.style.display = 'flex';
      var span = banner.querySelector('span');
      if (span) span.textContent = message;
    }
    showToast(message, 'error');
  }

  // ── API Helper ──
  async function apiPost(url, data) {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    const json = await res.json();
    return { ok: res.ok, status: res.status, data: json };
  }

  // ── Login Form Handler ──
  const loginForm = document.getElementById('login-form');
  if (loginForm) {
    loginForm.addEventListener('submit', async function (e) {
      e.preventDefault();
      clearAllErrors();

      const email = document.getElementById('login-email').value.trim();
      const password = document.getElementById('login-password').value;
      const btn = document.getElementById('login-btn');
      const errorBanner = document.getElementById('login-error');

      // Validation
      let hasError = false;

      if (!email) {
        showFieldError('login-email', 'Email is required');
        hasError = true;
      } else if (!isValidEmail(email)) {
        showFieldError('login-email', 'Please enter a valid email');
        hasError = true;
      }

      if (!password) {
        showFieldError('login-password', 'Password is required');
        hasError = true;
      }

      if (hasError) return;

      // Loading state
      btn.classList.add('loading');
      btn.querySelector('.btn-text').textContent = 'Signing in…';
      if (errorBanner) errorBanner.style.display = 'none';

      try {
        var result = await apiPost('/auth/login', {
          email: email,
          password: password,
        });

        if (!result.ok) {
          btn.classList.remove('loading');
          btn.querySelector('.btn-text').textContent = 'Sign in';

          // Check if user needs verification
          if (result.data && result.data.needs_verification) {
            showError(errorBanner, 'Please verify your email first. A new verification code has been sent.');
            // Redirect to signup with verification flow
            setTimeout(function () {
              window.location.href = '/signup?verify=' + encodeURIComponent(email);
            }, 1500);
            return;
          }

          showError(errorBanner, result.data.error || 'Invalid email or password. Please try again.');
          return;
        }

        // Success
        showToast(result.data.message || 'Welcome back! Redirecting…', 'success');
        setTimeout(function () {
          window.location.href = getNextUrl();
        }, 800);
      } catch (err) {
        btn.classList.remove('loading');
        btn.querySelector('.btn-text').textContent = 'Sign in';
        showError(errorBanner, 'Connection error. Please check your internet and try again.');
      }
    });
  }

  // ── Signup: form → verification code → complete flow ──
  let currentSignupEmail = '';
  let isVerificationNeeded = false;

  const signupForm = document.getElementById('signup-form');
  if (signupForm) {
    signupForm.addEventListener('submit', async function (e) {
      e.preventDefault();
      clearAllErrors();

      const name = document.getElementById('signup-name').value.trim();
      const email = document.getElementById('signup-email').value.trim();
      const password = document.getElementById('signup-password').value;
      const confirmPassword = document.getElementById('signup-confirm-password').value;
      const btn = document.getElementById('signup-btn');
      const errorBanner = document.getElementById('signup-error');

      // Validation
      let hasError = false;

      if (!name) {
        showFieldError('signup-name', 'Full name is required');
        hasError = true;
      } else if (name.length < 2) {
        showFieldError('signup-name', 'Name must be at least 2 characters');
        hasError = true;
      }

      if (!email) {
        showFieldError('signup-email', 'Email is required');
        hasError = true;
      } else if (!isValidEmail(email)) {
        showFieldError('signup-email', 'Please enter a valid email');
        hasError = true;
      }

      if (!password) {
        showFieldError('signup-password', 'Password is required');
        hasError = true;
      } else if (password.length < 8) {
        showFieldError('signup-password', 'Password must be at least 8 characters');
        hasError = true;
      } else if (calculatePasswordStrength(password) < 2) {
        showFieldError('signup-password', 'Password is too weak. Add uppercase, numbers, or symbols.');
        hasError = true;
      }

      if (!confirmPassword) {
        showFieldError('signup-confirm-password', 'Please confirm your password');
        hasError = true;
      } else if (password !== confirmPassword) {
        showFieldError('signup-confirm-password', 'Passwords do not match');
        hasError = true;
      }

      if (hasError) return;

      // Split name into first/last
      const nameParts = name.split(' ');
      const firstName = nameParts[0];
      const lastName = nameParts.slice(1).join(' ') || '';

      // Loading state
      btn.classList.add('loading');
      btn.querySelector('.btn-text').textContent = 'Creating account…';
      if (errorBanner) errorBanner.style.display = 'none';

      try {
        var result = await apiPost('/auth/signup', {
          email: email,
          password: password,
          first_name: firstName,
          last_name: lastName,
        });

        if (!result.ok) {
          btn.classList.remove('loading');
          btn.querySelector('.btn-text').textContent = 'Create account';
          showError(errorBanner, result.data.error || 'Something went wrong. Please try again.');
          return;
        }

        // Account created — show verification code input
        currentSignupEmail = email;

        document.getElementById('signup-form-section').classList.add('hidden');
        document.getElementById('verify-section').classList.add('active');
        document.getElementById('verify-email-display').textContent = email;
        document.getElementById('signup-heading').textContent = 'Check your email';
        document.getElementById('signup-subheading').textContent = 'We sent a 6-digit code to your email';

        btn.classList.remove('loading');
        btn.querySelector('.btn-text').textContent = 'Create account';

        setTimeout(function () {
          var firstInput = document.getElementById('code-0');
          if (firstInput) firstInput.focus();
        }, 100);

        showToast('Verification code sent to ' + email, 'info');
      } catch (err) {
        btn.classList.remove('loading');
        btn.querySelector('.btn-text').textContent = 'Create account';
        showError(errorBanner, 'Connection error. Please check your internet and try again.');
      }
    });
  }

  // ── Verify Code Button Handler ──
  var verifyBtn = document.getElementById('verify-btn');
  if (verifyBtn) {
    verifyBtn.addEventListener('click', async function () {
      var code = '';
      for (var i = 0; i < 6; i++) {
        var inp = document.getElementById('code-' + i);
        if (inp) code += inp.value;
      }

      if (code.length !== 6) {
        showToast('Please enter the full 6-digit code from your email.', 'error');
        return;
      }

      var btn = document.getElementById('verify-btn');
      var errorBanner = document.getElementById('signup-error');
      if (errorBanner) errorBanner.style.display = 'none';

      btn.classList.add('loading');
      btn.querySelector('.btn-text').textContent = 'Verifying…';

      try {
        var result = await apiPost('/auth/verify', {
          email: currentSignupEmail,
          code: code,
        });

        if (!result.ok) {
          btn.classList.remove('loading');
          btn.querySelector('.btn-text').textContent = 'Verify email';
          showError(errorBanner, result.data.error || 'Invalid code. Please try again.');
          return;
        }

        // Verified!
        showToast('Email verified! Welcome to CollegeKhoj. 🎉', 'success');
        setTimeout(function () { window.location.href = getNextUrl(); }, 800);
      } catch (err) {
        btn.classList.remove('loading');
        btn.querySelector('.btn-text').textContent = 'Verify email';
        showError(errorBanner, 'Connection error. Please try again.');
      }
    });
  }

  // ── Code Input Auto-Advance ──
  document.addEventListener('input', function (e) {
    if (e.target.classList.contains('code-input')) {
      e.target.classList.add('filled');
      var next = e.target.nextElementSibling;
      if (next && next.classList.contains('code-input') && e.target.value.length === 1) {
        next.focus();
      }
    }
  });

  document.addEventListener('keydown', function (e) {
    if (e.target.classList.contains('code-input') && e.key === 'Backspace') {
      if (e.target.value === '') {
        var prev = e.target.previousElementSibling;
        if (prev && prev.classList.contains('code-input')) {
          prev.focus();
          prev.value = '';
          prev.classList.remove('filled');
        }
      }
    }
    if (e.key === 'Enter' && e.target.id === 'code-5') {
      var verifyBtn = document.getElementById('verify-btn');
      if (verifyBtn) verifyBtn.click();
    }
  });

  // ── Resend Code Handler ──
  var resendLink = document.getElementById('resend-link');
  if (resendLink) {
    resendLink.addEventListener('click', async function () {
      if (!currentSignupEmail) {
        showToast('Session expired. Please go back and sign up again.', 'error');
        return;
      }
      try {
        var result = await apiPost('/auth/resend-code', {
          email: currentSignupEmail,
        });
        if (result.ok) {
          showToast('New verification code sent to ' + currentSignupEmail, 'info');
        } else {
          showToast(result.data.error || 'Failed to resend code.', 'error');
        }
      } catch (err) {
        showToast('Failed to resend code. Please try again.', 'error');
      }
    });
  }

  // ── Back to Signup Link ──
  var backToSignup = document.getElementById('back-to-signup-link');
  if (backToSignup) {
    backToSignup.addEventListener('click', function (e) {
      e.preventDefault();
      document.getElementById('verify-section').classList.remove('active');
      document.getElementById('signup-form-section').classList.remove('hidden');
      document.getElementById('signup-heading').textContent = 'Create your account';
      document.getElementById('signup-subheading').textContent = 'Join thousands of Maharashtra students finding their perfect college';
      currentSignupEmail = '';
      for (var i = 0; i < 6; i++) {
        var inp = document.getElementById('code-' + i);
        if (inp) { inp.value = ''; inp.classList.remove('filled'); }
      }
    });
  }

  // ── Forgot Password Handler ──
  // (Linked from login form)
  window.handleForgotPassword = async function () {
    const emailInput = document.getElementById('login-email');
    const email = emailInput ? emailInput.value.trim() : '';
    
    if (!email || !isValidEmail(email)) {
      showToast('Please enter a valid email address first.', 'error');
      if (emailInput) emailInput.focus();
      return;
    }

    try {
      var result = await apiPost('/auth/reset-password', { email: email });
      showToast(result.data.message || 'If an account exists, you will receive a reset link.', 'info');
    } catch (err) {
      showToast('Something went wrong. Please try again.', 'error');
    }
  };

  // ── Helper: Get next_url ──
  function getNextUrl() {
    const input = document.getElementById('next-url');
    return input ? input.value : '/mhcet';
  }

  // ── Social Login Handlers ──
  document.addEventListener('click', function (e) {
    const socialBtn = e.target.closest('.social-btn');
    if (!socialBtn) return;

    const provider = socialBtn.getAttribute('data-provider');
    if (!provider) return;

    showToast(provider + ' sign-in coming soon! Please use email instead.', 'info');
  });

  // ── Real-time Validation on Blur ──
  document.addEventListener('blur', function (e) {
    const field = e.target;
    if (!field.classList.contains('input-field')) return;
    if (!field.closest('.auth-card')) return;

    const id = field.id;
    if (!id) return;

    const value = field.value.trim();

    if (id === 'login-email' || id === 'signup-email') {
      if (value && !isValidEmail(value)) {
        showFieldError(id, 'Please enter a valid email');
      } else {
        clearFieldError(id);
      }
    }

    if (id === 'signup-name') {
      if (value && value.length < 2) {
        showFieldError(id, 'Name must be at least 2 characters');
      } else {
        clearFieldError(id);
      }
    }

    if (id === 'login-password') {
      if (value && value.length < 1) {
        showFieldError(id, 'Password is required');
      } else {
        clearFieldError(id);
      }
    }

    if (id === 'signup-password') {
      if (value && value.length < 8) {
        showFieldError(id, 'Password must be at least 8 characters');
      } else if (value && calculatePasswordStrength(value) < 2) {
        showFieldError(id, 'Password is too weak');
      } else {
        clearFieldError(id);
      }
    }

    if (id === 'signup-confirm-password') {
      checkPasswordMatch();
    }
  }, true);

  // ── Flash Messages → Toast ──
  document.addEventListener('DOMContentLoaded', function () {
    const flashData = document.getElementById('flash-data');
    if (flashData) {
      try {
        const messages = JSON.parse(flashData.textContent);
        messages.forEach(function (msg) {
          showToast(msg.text, msg.category === 'error' ? 'error' : 'success');
        });
      } catch (e) {
        // ignore
      }
    }
  });

})();