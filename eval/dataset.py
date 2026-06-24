"""
dataset.py — Evaluation dataset required by task spec section 7.

10 real product prompts (realistic, well-specified app requests) +
10 edge cases split across vague / conflicting / incomplete, as explicitly
required. Each entry is tagged with its category so eval/run_eval.py can
break down success rate BY category, not just in aggregate — aggregate-only
numbers would hide exactly the failure modes this task cares about.
"""

REAL_PROMPTS = [
    {
        "id": "real_01",
        "category": "real",
        "prompt": "Build a CRM with login, contacts, dashboard, role-based access, and premium plan with payments. Admins can see analytics.",
    },
    {
        "id": "real_02",
        "category": "real",
        "prompt": "Create a project management tool where teams can create projects, assign tasks to members, set deadlines, and track progress on a kanban board.",
    },
    {
        "id": "real_03",
        "category": "real",
        "prompt": "I need a blog platform. Writers can publish posts, readers can comment, and there's an admin who can moderate and delete inappropriate comments.",
    },
    {
        "id": "real_04",
        "category": "real",
        "prompt": "Build an e-commerce marketplace where sellers list products, buyers browse and purchase, and admins can ban sellers who violate policy.",
    },
    {
        "id": "real_05",
        "category": "real",
        "prompt": "Make a job board: companies post job listings, candidates apply with a resume, and recruiters can view applicants and shortlist them.",
    },
    {
        "id": "real_06",
        "category": "real",
        "prompt": "Build a fitness tracking app where users log workouts, set goals, and view progress charts. There's a free tier and a premium tier that unlocks advanced analytics.",
    },
    {
        "id": "real_07",
        "category": "real",
        "prompt": "Create a customer support ticketing system. Customers submit tickets, support agents respond and resolve them, and managers can see SLA dashboards.",
    },
    {
        "id": "real_08",
        "category": "real",
        "prompt": "Build an event management platform where organizers create events and sell tickets, and attendees can register and pay for tickets online.",
    },
    {
        "id": "real_09",
        "category": "real",
        "prompt": "I want a learning management system: instructors create courses with lessons, students enroll and track their progress, and admins manage instructor approvals.",
    },
    {
        "id": "real_10",
        "category": "real",
        "prompt": "Build a real estate listing site where agents list properties, buyers can search and save favorites, and there's a paid 'featured listing' upgrade for agents.",
    },
    # --- Edge cases: vague ---
    {
        "id": "edge_vague_01",
        "category": "vague",
        "prompt": "Build me an app.",
    },
    {
        "id": "edge_vague_02",
        "category": "vague",
        "prompt": "Something for tracking stuff for my small business.",
    },
    {
        "id": "edge_vague_03",
        "category": "vague",
        "prompt": "I want an app like Notion but simpler.",
    },
    # --- Edge cases: conflicting ---
    {
        "id": "edge_conflict_01",
        "category": "conflicting",
        "prompt": "Build an app with no login required, but every page should be restricted to admins only and show personalized user analytics.",
    },
    {
        "id": "edge_conflict_02",
        "category": "conflicting",
        "prompt": "Make a free app with no payments anywhere, but include a premium subscription plan that unlocks paid features.",
    },
    {
        "id": "edge_conflict_03",
        "category": "conflicting",
        "prompt": "Build a public read-only blog where anyone can read posts without an account, but also every reader must have a role-based permission level to view anything.",
    },
    {
        "id": "edge_conflict_04",
        "category": "conflicting",
        "prompt": "Create a single-user personal journal app, but also it needs multi-tenant organizations with admin, manager, and employee roles.",
    },
    # --- Edge cases: incomplete ---
    {
        "id": "edge_incomplete_01",
        "category": "incomplete",
        "prompt": "Build a CRM.",
    },
    {
        "id": "edge_incomplete_02",
        "category": "incomplete",
        "prompt": "I need role-based access control and a dashboard.",
    },
    {
        "id": "edge_incomplete_03",
        "category": "incomplete",
        "prompt": "Add a payments feature.",
    },
]
