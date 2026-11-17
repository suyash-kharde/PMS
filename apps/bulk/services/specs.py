"""Column definitions shared by the template builder and the validator, so a
header can never drift between the sheet we hand out and the sheet we read back.

Each column is (field_key, header_text, help_text). A header ending in "*" is required;
the "*" is stripped for matching so the uploaded file's header doesn't need to include it.
"""

LABELS = {"customers": "Customers", "projects": "Projects", "people": "People"}

CAPABILITY = {"customers": "customer.manage", "projects": "project.create", "people": "user.create"}

# creation order for the combined sheet: customers first so projects/people can
# reference one by name, then people so projects can reference a person as owner
KIND_ORDER = ["customers", "people", "projects"]

CUSTOMERS = [
    ("name", "Name*", "Company / client name. Required, must be unique."),
    ("code", "Code", "Short key like ACME. Auto-generated from the name if left blank."),
    ("industry", "Industry", "Free text, e.g. Manufacturing."),
    ("website", "Website", "e.g. https://acme.example.com"),
    ("account_manager", "Account manager (username or email)", "Must match an existing user's username or email. Left blank if not found."),
    ("primary_contact", "Primary contact (username or email)", "Must match an existing user's username or email. Left blank if not found."),
    ("contact_email", "Contact email", ""),
    ("contact_phone", "Contact phone", ""),
    ("is_active", "Active (TRUE/FALSE)", "Defaults to TRUE."),
    ("description", "Description", ""),
    ("notes", "Notes", ""),
]

PROJECTS = [
    ("name", "Name*", "Required."),
    ("key", "Key", "Short unique code like ACME1. Auto-generated from the name if left blank."),
    ("customer", "Customer (name)", "Must match an existing customer by name exactly. Left blank if not found."),
    ("description", "Description", ""),
    ("status", "Status", "One of: lead, proposal (Proposal Submitted), active, under_amc (Under AMC), completed. Defaults to lead."),
    ("priority", "Priority", "One of: Critical, High, Medium, Low. Defaults to Medium."),
    ("owner", "Owner (username or email)", "Must match an existing user's username or email. Left blank if not found."),
    ("team", "Team (name)", "Must match an existing team by name exactly. Left blank if not found."),
    ("start_date", "Start date (YYYY-MM-DD)", ""),
    ("due_date", "Due date (YYYY-MM-DD)", "Must be on or after the start date."),
    ("budget", "Budget", "Plain number, e.g. 25000."),
]

PEOPLE = [
    ("first_name", "First name*", "Required."),
    ("last_name", "Last name", ""),
    ("username", "Username", "Auto-derived from the name if left blank."),
    ("email", "Email", ""),
    ("phone", "Phone", ""),
    ("role", "Role (name)", "Must match an existing role by name exactly. Left blank if not found."),
    ("team", "Team (name)", "Must match an existing team by name exactly. Left blank if not found."),
    ("manager", "Manager (username or email)", "Must match an existing user's username or email. Left blank if not found."),
    ("customer", "Customer (name)", "For client-side people. Must match an existing customer by name exactly."),
    ("title", "Title", "Job title, e.g. Product Owner."),
    ("description", "Description", ""),
    ("responsibility", "Responsibility", ""),
    ("remark", "Remark", ""),
    ("is_active", "Active (TRUE/FALSE)", "Defaults to TRUE."),
    ("password", "Password", "Leave blank to auto-generate a temporary password shown after import."),
]

COLUMNS = {"customers": CUSTOMERS, "projects": PROJECTS, "people": PEOPLE}

EXAMPLES = {
    "customers": [
        ["Acme Corp", "", "Manufacturing", "https://acme.example.com", "jane.pm", "", "ops@acme.example.com", "+1 555 0100", "TRUE", "Long-time client", "Prefers async updates"],
    ],
    "projects": [
        ["Acme Website Revamp", "", "Acme Corp", "Rebuild the public site", "lead", "High", "jane.pm", "", "2026-09-01", "2026-12-01", "50000"],
    ],
    "people": [
        ["Priya", "Shah", "", "priya.shah@example.com", "+1 555 0101", "Project Manager", "", "jane.pm", "Acme Corp", "Client PM", "", "", "", "TRUE", ""],
    ],
}
