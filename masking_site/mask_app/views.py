from django.shortcuts import render, redirect
from django.http import HttpResponse
from django.core.mail import send_mail
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from .forms import UploadFileForm, ColumnSelectForm
import pandas as pd
import random
import string
import io
import base64
import re
import hashlib
import secrets
import stripe
from rest_framework.decorators import api_view
from rest_framework.response import Response

stripe.api_key = settings.STRIPE_SECRET_KEY


# ── Core masking function ───────────────────────────────────────

def mask_value(value, mapping):
    if value in mapping:
        return mapping[value]

    if isinstance(value, str) and len(value) > 1:
        value_list = list(value)
        letter_indices = [i for i, c in enumerate(value_list) if c.isalpha()]
        if len(letter_indices) >= 2:
            idx1, idx2 = random.sample(letter_indices, 2)
            value_list[idx1] = random.choice(string.ascii_letters)
            value_list[idx2] = random.choice(string.ascii_letters)
        new_value = ''.join(value_list)
    elif isinstance(value, (int, float)):
        value_str = str(value)
        digit_indices = [i for i, c in enumerate(value_str) if c.isdigit()]
        if len(digit_indices) >= 2:
            idx1, idx2 = random.sample(digit_indices, 2)
            value_list = list(value_str)
            value_list[idx1] = random.choice(string.digits)
            value_list[idx2] = random.choice(string.digits)
            joined = ''.join(value_list)
            new_value = float(joined) if '.' in value_str else int(joined)
        else:
            new_value = value
    elif isinstance(value, pd.Timestamp):
        new_value = value + pd.DateOffset(days=random.randint(1, 365))
    else:
        new_value = value

    mapping[value] = new_value
    return new_value


# ── Web pages ───────────────────────────────────────────────────

def looks_headerless(df):
    """True if most column names are pandas placeholders."""
    if len(df.columns) == 0:
        return False
    unnamed = sum(1 for c in df.columns if str(c).startswith('Unnamed:'))
    return unnamed > len(df.columns) / 2

def home(request):
    return render(request, 'index.html')


def upload_file(request):
    if request.method != 'POST':
        return redirect('home')

    form = UploadFileForm(request.POST, request.FILES)
    if not form.is_valid():
        return redirect('home')

    file = request.FILES['file']
    name = file.name.lower()

    # ── Read every sheet ──
    try:
        if name.endswith('.csv'):
            sheets = {'Sheet1': pd.read_csv(file)}
            is_csv = True
        elif name.endswith(('.xls', '.xlsx')):
            sheets = pd.read_excel(file, sheet_name=None)
            is_csv = False
        elif name.endswith('.xml'):
            sheets = {'Sheet1': pd.read_xml(file)}
            is_csv = False
        else:
            return HttpResponse("Unsupported file format. Please upload CSV, Excel or XML.")

        # ── Fix sheets with no real header row ──
        for sheet in list(sheets.keys()):
            if looks_headerless(sheets[sheet]):
                file.seek(0)
                if is_csv:
                    fixed = pd.read_csv(file, header=None)
                else:
                    fixed = pd.read_excel(file, sheet_name=sheet, header=None)
                fixed.columns = [f"Column {i+1}" for i in range(len(fixed.columns))]
                sheets[sheet] = fixed

    except Exception as e:
        return HttpResponse(f"Could not read that file: {e}")

    # ── Store all sheets in session, run detection ──
    request.session['sheets'] = {n: df.to_json() for n, df in sheets.items()}

    sheet_data = []
    for sheet_name, df in sheets.items():
        detected = auto_detect_pii(df)
        sheet_data.append({
            'name': sheet_name,
            'rows': len(df),
            'columns': [
                {
                    'key': f"{sheet_name}||{col}",   # unique across sheets
                    'label': str(col),
                    'detected': col in detected,
                }
                for col in df.columns
            ]
        })

    total_detected = sum(
        1 for s in sheet_data for c in s['columns'] if c['detected']
    )

    return render(request, 'mask_app/select_columns.html', {
        'sheet_data': sheet_data,
        'total_detected': total_detected,
        'sheet_count': len(sheets),
    })

def mask_columns(request):
    if request.method != 'POST':
        return redirect('home')

    stored = request.session.get('sheets')
    if not stored:
        return HttpResponse("Your session expired. Please upload the file again.")

    sheets = {name: pd.read_json(io.StringIO(j)) for name, j in stored.items()}

    selected = request.POST.getlist('columns')
    if not selected:
        return HttpResponse("No columns selected — nothing to mask.")

    by_sheet = {}
    for item in selected:
        if '||' not in item:
            continue
        sheet_name, col = item.split('||', 1)
        by_sheet.setdefault(sheet_name, []).append(col)

    mapping = {}
    masked_by_sheet = {}

    try:
        for sheet_name, df in sheets.items():
            cols = by_sheet.get(sheet_name, [])
            for col in cols:
                if col not in df.columns:
                    continue
                approach = request.POST.get(f"approach_{sheet_name}||{col}", 'fpe')
                if approach == 'xxx':
                    df[col] = "XXX"
                else:
                    df[col] = df[col].apply(lambda v: mask_value(v, mapping))
            sheets[sheet_name] = df
            masked_by_sheet[sheet_name] = cols
    except Exception as e:
        return HttpResponse(f"Error while masking: {e}")

    # ── Build the Excel file and keep it in session for download ──
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        for sheet_name, df in sheets.items():
            df.to_excel(writer, index=False, sheet_name=str(sheet_name)[:31])
        summary = []
        for sheet_name, masked in masked_by_sheet.items():
            for col in sheets[sheet_name].columns:
                summary.append({
                    'Sheet':  sheet_name,
                    'Column': col,
                    'Status': 'MASKED' if col in masked else 'unchanged',
                })
        pd.DataFrame(summary).to_excel(writer, index=False, sheet_name='DataRepli Summary')
    output.seek(0)
    request.session['masked_file'] = base64.b64encode(output.read()).decode()

    # ── Build Markdown tables (capped so the page stays usable) ──
    PREVIEW_ROWS = 500
    md_blocks = []
    truncated = False
    for sheet_name, df in sheets.items():
        shown = df.head(PREVIEW_ROWS)
        if len(df) > PREVIEW_ROWS:
            truncated = True
        block = shown.to_markdown(index=False) if len(sheets) == 1 else \
                f"### {sheet_name}\n\n" + shown.to_markdown(index=False)
        md_blocks.append(block)
    markdown_output = "\n\n".join(md_blocks)

    total_rows = sum(len(df) for df in sheets.values())
    total_masked = sum(len(c) for c in masked_by_sheet.values())

    return render(request, 'mask_app/results.html', {
        'markdown': markdown_output,
        'sheets': [
            {'name': n, 'rows': len(sheets[n]), 'masked': masked_by_sheet.get(n, [])}
            for n in sheets
        ],
        'total_rows': total_rows,
        'total_masked': total_masked,
        'truncated': truncated,
        'preview_rows': PREVIEW_ROWS,
    })


def contact_view(request):
    if request.method != 'POST':
        return redirect('home')

    from .models import ContactMessage

    name = (request.POST.get('name') or '').strip()
    email = (request.POST.get('email') or '').strip()
    message = (request.POST.get('message') or '').strip()

    if not (name and email and message):
        return render(request, 'contact_done.html', {'ok': False})

    try:
        ContactMessage.objects.create(name=name, email=email, message=message)
        saved = True
    except Exception as e:
        print(f"Contact save failed: {e}")
        saved = False

    # try to email too — but never let it break the page
    try:
        send_mail(
            subject=f"DataRepli enquiry from {name}",
            message=f"From: {name} <{email}>\n\n{message}",
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=['daniilforsteam@gmail.com'],
            fail_silently=True
        )
    except Exception:
        pass

    return render(request, 'contact_done.html', {'ok': saved})


def success(request):
    from .models import ApiKey
    session_id = request.GET.get('session_id')

    api_key = None
    tier = None
    email = None

    if session_id:
        try:
            session = stripe.checkout.Session.retrieve(session_id)
            customer_id = session.get('customer')
            key_record = ApiKey.objects.filter(
                stripe_customer_id=customer_id
            ).order_by('-created_at').first()
            if key_record:
                api_key = key_record.raw_key_temp
                tier = key_record.tier
                email = key_record.email
        except Exception as e:
            print(f"Success page error: {e}")

    return render(request, 'success.html', {
        'api_key': api_key,
        'tier': tier,
        'email': email,
    })


# ── PII auto-detection ──────────────────────────────────────────

PII_PATTERNS = {
    'name':    ['name', 'nimi', 'namn', 'first_name', 'last_name',
                'full_name', 'firstname', 'lastname', 'forename', 'surname',
                'approved_by', 'created_by', 'requested_by', 'signed_by',
                'owner', 'contact', 'manager', 'employee', 'signatory',
                'customer', 'client', 'person', 'recipient', 'sender'],

    'email':   ['email', 'e-mail', 'mail_address', 'epost', 'courriel'],

    'phone':   ['phone', 'tel', 'telephone', 'mobile', 'telefon',
                'gsm', 'cell', 'fax'],

    # removed bare 'account' / 'konto' — they collide with accounting terms
    'iban':    ['iban', 'bank_account', 'account_number', 'accountno',
                'acct_no', 'kontonummer', 'bankkonto', 'swift', 'bic',
                'card_number', 'creditcard'],

    'ssn':     ['ssn', 'social_security', 'personal_code', 'id_number',
                'national_id', 'personnummer', 'tax_id', 'taxid',
                'vat_id', 'vatno', 'tin', 'nino', 'passport'],

    'address': ['address', 'street', 'city', 'postcode', 'zipcode',
                'postal_code', 'zip', 'adresse', 'residence'],

    'dob':     ['dob', 'birth', 'birthday', 'date_of_birth', 'birthdate'],
}

def scan_values_for_pii(series):
    sample = series.dropna().astype(str).head(30)
    for val in sample:
        if re.match(r'[A-Z]{2}\d{2}[A-Z0-9]{4}\d{7}', val):
            return 'iban'
        if re.match(r'[\w.\-]+@[\w.\-]+\.\w{2,}', val):
            return 'email'
        if re.match(r'\+\d{7,14}', val):
            return 'phone'
        if re.match(r'\d{6}[-+A]\d{3}[A-Z0-9]', val):
            return 'ssn'
    return None

def auto_detect_pii(df):
    rules = {}
    for col in df.columns:
        col_lower = str(col).lower().strip().replace(' ', '_')
        for field_type, patterns in PII_PATTERNS.items():
            if any(p in col_lower for p in patterns):
                rules[col] = 'mask'
                break
        if col not in rules:
            detected = scan_values_for_pii(df[col])
            if detected:
                rules[col] = 'mask'
    return rules


# ── File masking API endpoint ───────────────────────────────────

@api_view(['POST'])
def mask_file_api(request):
    auth = request.headers.get('Authorization', '')
    if not auth.startswith('Bearer '):
        return Response({'error': 'Missing API key'}, status=401)

    raw_key = auth[7:]
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

    from .models import ApiKey, UsageLog
    try:
        key_record = ApiKey.objects.get(key_hash=key_hash, active=True)
    except ApiKey.DoesNotExist:
        return Response({'error': 'Invalid API key'}, status=401)

    # ── Tier enforcement ──
    TIER_LIMITS = {
        'free':       0,
        'pro':        500_000,
        'business':   5_000_000,
        'enterprise': None,
    }
    tier = key_record.tier
    limit = TIER_LIMITS.get(tier, 0)

    if limit == 0:
        return Response({
            'error': 'API access requires a paid plan',
            'upgrade_url': 'https://www.datarepli.com/#pricing'
        }, status=403)

    file_b64  = request.data.get('file_base64')
    file_type = request.data.get('file_type', 'xlsx').lower()
    auto      = request.data.get('auto_detect', True)

    if not file_b64:
        return Response({'error': 'No file_base64 provided'}, status=400)

    # ── Read ALL sheets ──
    try:
        file_bytes = base64.b64decode(file_b64)
        file_obj = io.BytesIO(file_bytes)
        if file_type == 'csv':
            sheets = {'Sheet1': pd.read_csv(file_obj)}
        else:
            sheets = pd.read_excel(file_obj, sheet_name=None)

        # ── Fix sheets that have no real header row ──
        for name in list(sheets.keys()):
            if looks_headerless(sheets[name]):
                file_obj.seek(0)
                if file_type == 'csv':
                    fixed = pd.read_csv(file_obj, header=None)
                else:
                    fixed = pd.read_excel(file_obj, sheet_name=name, header=None)
                fixed.columns = [f"Column {i+1}" for i in range(len(fixed.columns))]
                sheets[name] = fixed

    except Exception as e:
        return Response({'error': f'Could not read file: {e}'}, status=400)

    force_mask = request.data.get('force_mask', [])
    never_mask = request.data.get('never_mask', [])

    # shared mapping across ALL sheets = referential integrity
    mapping = {}
    total_rows = 0
    all_masked_fields = {}

    # ── Mask every sheet ──
    for sheet_name, df in sheets.items():
        if auto:
            rules = auto_detect_pii(df)
        else:
            rules = dict(request.data.get('rules', {}))

        for col in force_mask:
            if col in df.columns:
                rules[col] = 'mask'
        for col in never_mask:
            rules.pop(col, None)

        for col, strategy in rules.items():
            if col not in df.columns:
                continue
            if strategy == 'redact':
                df[col] = '***'
            elif strategy == 'mask':
                df[col] = df[col].apply(lambda v: mask_value(v, mapping))

        sheets[sheet_name] = df
        all_masked_fields[sheet_name] = list(rules.keys())
        total_rows += len(df)

    # ── Row limit check (total across all sheets) ──
    if limit is not None and (key_record.rows_used_this_month + total_rows) > limit:
        return Response({
            'error': f'Monthly row limit exceeded for {tier} tier',
            'limit': limit,
            'used': key_record.rows_used_this_month,
            'upgrade_url': 'https://www.datarepli.com/#pricing'
        }, status=429)

    # ── Write output ──
    output = io.BytesIO()
    if file_type == 'csv':
        list(sheets.values())[0].to_csv(output, index=False)
        mime = 'text/csv'
    else:
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            for sheet_name, df in sheets.items():
                df.to_excel(writer, index=False, sheet_name=str(sheet_name)[:31])

            summary_rows = []
            for sheet_name, fields in all_masked_fields.items():
                for col in sheets[sheet_name].columns:
                    summary_rows.append({
                        'Sheet':  sheet_name,
                        'Column': col,
                        'Status': 'MASKED' if col in fields else 'unchanged',
                        'Reason': 'PII detected' if col in fields else 'no PII detected'
                    })
            pd.DataFrame(summary_rows).to_excel(
                writer, index=False, sheet_name='DataRepli Summary')
        mime = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'

    output.seek(0)
    encoded = base64.b64encode(output.read()).decode('utf-8')

    # ── Update usage ──
    key_record.rows_used_this_month += total_rows
    key_record.save()

    flat_fields = sorted({c for fields in all_masked_fields.values() for c in fields})
    UsageLog.objects.create(
        api_key=key_record,
        rows_processed=total_rows,
        fields_masked=flat_fields
    )

    return Response({
        'status':          'done',
        'sheets_processed': len(sheets),
        'rows_processed':  total_rows,
        'fields_masked':   all_masked_fields,
        'file_type':       file_type,
        'mime_type':       mime,
        'file_base64':     encoded,
    })

# ── Stripe webhook ──────────────────────────────────────────────

@csrf_exempt
def stripe_webhook(request):
    from .models import ApiKey

    payload = request.body
    sig_header = request.headers.get('Stripe-Signature') or request.META.get('HTTP_STRIPE_SIGNATURE')
    webhook_secret = settings.STRIPE_WEBHOOK_SECRET

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except Exception as e:
        return HttpResponse(f"Webhook error: {e}", status=400)

    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        customer_email = session.get('customer_details', {}).get('email', 'unknown')
        amount = session.get('amount_total', 0)

        if amount >= 29900:
            tier = 'business'
        elif amount >= 4900:
            tier = 'pro'
        else:
            tier = 'free'

        raw_key = 'dk_live_' + secrets.token_urlsafe(24)
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

        try:
            ApiKey.objects.create(
                name=f"{tier} subscription",
                key_hash=key_hash,
                email=customer_email,
                tier=tier,
                active=True,
                stripe_customer_id=session.get('customer', ''),
                raw_key_temp=raw_key
            )
        except Exception as e:
            print(f"KEY CREATION FAILED: {e}")

        try:
            send_mail(
                subject="Your DataRepli API Key",
                message=(
                    f"Welcome to DataRepli {tier.title()}!\n\n"
                    f"Your API key is:\n\n{raw_key}\n\n"
                    f"Keep this safe.\n\n"
                    f"Use it in the Authorization header:\n"
                    f"Authorization: Bearer {raw_key}\n\n"
                    f"— DataRepli"
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[customer_email],
                fail_silently=True
            )
        except Exception as e:
            print(f"Email failed: {e}")

    elif event['type'] in ('invoice.payment_failed', 'customer.subscription.deleted'):
        obj = event['data']['object']
        customer_id = obj.get('customer', '')
        ApiKey.objects.filter(stripe_customer_id=customer_id).update(active=False)

    return HttpResponse(status=200)
def docs(request):
    return render(request, 'docs.html')

def download_masked(request):
    encoded = request.session.get('masked_file')
    if not encoded:
        return redirect('home')
    data = base64.b64decode(encoded)
    response = HttpResponse(
        data,
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = 'attachment; filename="masked_data.xlsx"'
    return response