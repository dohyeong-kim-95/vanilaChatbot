"""Loop 11 fixture: a realistic many-MCP setup. 23 servers, ~130 tools, many near-duplicates across servers
(issue trackers, chat, mail, calendars, docs, SQL, CRM). Only schemas — the experiment scores the first call."""

import json

S = "string"
SERVERS = {
    "github": ("GitHub repositories, issues and pull requests", [
        ("create_issue", "Create an issue in a GitHub repository.", {"repo": "owner/name", "title": S, "body": S}, ["repo", "title"]),
        ("list_issues", "List issues in a GitHub repository, optionally filtered by state or label.", {"repo": "owner/name", "state": S, "label": S}, ["repo"]),
        ("get_issue", "Get one GitHub issue by number.", {"repo": "owner/name", "number": "integer"}, ["repo", "number"]),
        ("comment_on_issue", "Add a comment to a GitHub issue or pull request.", {"repo": "owner/name", "number": "integer", "body": S}, ["repo", "number", "body"]),
        ("create_pull_request", "Open a pull request from a head branch into a base branch.", {"repo": "owner/name", "head": S, "base": S, "title": S}, ["repo", "head", "base", "title"]),
        ("list_pull_requests", "List pull requests in a GitHub repository.", {"repo": "owner/name", "state": S}, ["repo"]),
        ("merge_pull_request", "Merge a GitHub pull request.", {"repo": "owner/name", "number": "integer", "method": "merge|squash|rebase"}, ["repo", "number"]),
        ("get_file_contents", "Read a file from a GitHub repository at a ref.", {"repo": "owner/name", "path": S, "ref": S}, ["repo", "path"]),
        ("search_code", "Search code across GitHub repositories.", {"query": S}, ["query"]),
        ("create_branch", "Create a branch in a GitHub repository.", {"repo": "owner/name", "branch": S, "from_ref": S}, ["repo", "branch"]),
        ("list_commits", "List commits on a branch of a GitHub repository.", {"repo": "owner/name", "branch": S}, ["repo"]),
        ("create_release", "Create a tagged release in a GitHub repository.", {"repo": "owner/name", "tag": S, "notes": S}, ["repo", "tag"]),
    ]),
    "gitlab": ("GitLab projects, issues, merge requests and CI pipelines", [
        ("create_issue", "Create an issue in a GitLab project.", {"project": "group/name", "title": S, "description": S}, ["project", "title"]),
        ("list_issues", "List issues in a GitLab project.", {"project": "group/name", "state": S}, ["project"]),
        ("create_merge_request", "Open a merge request in a GitLab project.", {"project": "group/name", "source_branch": S, "target_branch": S, "title": S}, ["project", "source_branch", "target_branch", "title"]),
        ("list_merge_requests", "List merge requests in a GitLab project.", {"project": "group/name", "state": S}, ["project"]),
        ("get_file", "Read a file from a GitLab project.", {"project": "group/name", "path": S, "ref": S}, ["project", "path"]),
        ("search_code", "Search code in GitLab.", {"query": S}, ["query"]),
        ("list_pipelines", "List CI/CD pipelines of a GitLab project.", {"project": "group/name", "status": S}, ["project"]),
        ("retry_pipeline", "Retry a failed GitLab CI pipeline.", {"project": "group/name", "pipeline_id": "integer"}, ["project", "pipeline_id"]),
    ]),
    "jira": ("Jira issues, sprints and workflows", [
        ("create_issue", "Create a Jira issue (bug, task, story) in a project.", {"project_key": S, "summary": S, "issue_type": S, "description": S}, ["project_key", "summary", "issue_type"]),
        ("search_issues", "Search Jira issues with JQL.", {"jql": S}, ["jql"]),
        ("transition_issue", "Move a Jira issue to another workflow status (e.g. In Progress, Done).", {"issue_key": S, "status": S}, ["issue_key", "status"]),
        ("add_comment", "Add a comment to a Jira issue.", {"issue_key": S, "body": S}, ["issue_key", "body"]),
        ("assign_issue", "Assign a Jira issue to a user.", {"issue_key": S, "assignee": S}, ["issue_key", "assignee"]),
        ("get_issue", "Get a Jira issue by key.", {"issue_key": S}, ["issue_key"]),
        ("create_sprint", "Create a sprint on a Jira board.", {"board_id": "integer", "name": S}, ["board_id", "name"]),
        ("list_sprints", "List sprints of a Jira board.", {"board_id": "integer"}, ["board_id"]),
    ]),
    "linear": ("Linear issues and cycles", [
        ("create_issue", "Create a Linear issue for a team.", {"team": S, "title": S, "description": S, "priority": "integer"}, ["team", "title"]),
        ("update_issue", "Update a Linear issue (state, assignee, priority).", {"issue_id": S, "state": S, "assignee": S}, ["issue_id"]),
        ("list_issues", "List Linear issues for a team.", {"team": S, "state": S}, ["team"]),
        ("add_comment", "Comment on a Linear issue.", {"issue_id": S, "body": S}, ["issue_id", "body"]),
        ("list_teams", "List Linear teams.", {}, []),
        ("list_cycles", "List cycles of a Linear team.", {"team": S}, ["team"]),
    ]),
    "slack": ("Slack messaging", [
        ("send_message", "Post a message to a Slack channel.", {"channel": S, "text": S}, ["channel", "text"]),
        ("send_dm", "Send a direct message to a Slack user.", {"user": S, "text": S}, ["user", "text"]),
        ("list_channels", "List Slack channels.", {}, []),
        ("search_messages", "Search Slack message history.", {"query": S}, ["query"]),
        ("add_reaction", "Add an emoji reaction to a Slack message.", {"channel": S, "ts": S, "emoji": S}, ["channel", "ts", "emoji"]),
        ("create_channel", "Create a Slack channel.", {"name": S, "private": "boolean"}, ["name"]),
        ("set_status", "Set your Slack status.", {"text": S, "emoji": S}, ["text"]),
        ("upload_file", "Upload a file to a Slack channel.", {"channel": S, "path": S}, ["channel", "path"]),
    ]),
    "teams": ("Microsoft Teams chats, channels and meetings", [
        ("send_channel_message", "Post a message to a Microsoft Teams channel.", {"team": S, "channel": S, "text": S}, ["team", "channel", "text"]),
        ("send_chat", "Send a 1:1 or group chat message in Microsoft Teams.", {"to": S, "text": S}, ["to", "text"]),
        ("list_teams", "List Microsoft Teams teams you belong to.", {}, []),
        ("schedule_meeting", "Schedule a Microsoft Teams online meeting.", {"title": S, "attendees": S, "start": S, "duration_min": "integer"}, ["title", "attendees", "start"]),
    ]),
    "gmail": ("Gmail", [
        ("send_email", "Send an email from Gmail.", {"to": S, "subject": S, "body": S}, ["to", "subject", "body"]),
        ("search_emails", "Search Gmail with Gmail query syntax.", {"query": S}, ["query"]),
        ("read_email", "Read a Gmail message by id.", {"id": S}, ["id"]),
        ("create_draft", "Create a Gmail draft without sending.", {"to": S, "subject": S, "body": S}, ["to", "subject", "body"]),
        ("add_label", "Add a label to a Gmail message.", {"id": S, "label": S}, ["id", "label"]),
    ]),
    "outlook": ("Outlook mail and calendar (Microsoft 365)", [
        ("send_mail", "Send an email from Outlook.", {"to": S, "subject": S, "body": S}, ["to", "subject", "body"]),
        ("list_messages", "List or search Outlook mailbox messages.", {"query": S}, []),
        ("create_event", "Create an Outlook calendar event.", {"title": S, "start": S, "end": S, "attendees": S}, ["title", "start", "end"]),
        ("list_events", "List Outlook calendar events in a range.", {"start": S, "end": S}, ["start", "end"]),
    ]),
    "gcal": ("Google Calendar", [
        ("create_event", "Create a Google Calendar event.", {"title": S, "start": S, "end": S, "attendees": S}, ["title", "start", "end"]),
        ("list_events", "List Google Calendar events in a range.", {"start": S, "end": S}, ["start", "end"]),
        ("update_event", "Update a Google Calendar event.", {"event_id": S, "start": S, "end": S, "title": S}, ["event_id"]),
        ("delete_event", "Delete a Google Calendar event.", {"event_id": S}, ["event_id"]),
        ("find_free_time", "Find common free time slots for attendees in Google Calendar.", {"attendees": S, "duration_min": "integer", "within_days": "integer"}, ["attendees", "duration_min"]),
    ]),
    "gdrive": ("Google Drive files and Google Docs", [
        ("search_files", "Search files in Google Drive by name or content.", {"query": S}, ["query"]),
        ("read_file", "Read the text of a Google Drive file / Google Doc.", {"file_id": S}, ["file_id"]),
        ("create_doc", "Create a Google Doc.", {"title": S, "content": S}, ["title"]),
        ("share_file", "Share a Google Drive file with people.", {"file_id": S, "email": S, "role": "reader|writer"}, ["file_id", "email"]),
        ("upload_file", "Upload a local file to Google Drive.", {"path": S, "folder_id": S}, ["path"]),
        ("move_file", "Move a Google Drive file to a folder.", {"file_id": S, "folder_id": S}, ["file_id", "folder_id"]),
    ]),
    "notion": ("Notion pages and databases", [
        ("search", "Search Notion pages and databases.", {"query": S}, ["query"]),
        ("get_page", "Get a Notion page's content.", {"page_id": S}, ["page_id"]),
        ("create_page", "Create a Notion page under a parent page.", {"parent_id": S, "title": S, "content": S}, ["parent_id", "title"]),
        ("append_block", "Append content blocks to a Notion page.", {"page_id": S, "content": S}, ["page_id", "content"]),
        ("query_database", "Query rows of a Notion database with a filter.", {"database_id": S, "filter": S}, ["database_id"]),
        ("update_database_row", "Update properties of a Notion database row.", {"row_id": S, "properties": S}, ["row_id", "properties"]),
    ]),
    "confluence": ("Confluence wiki", [
        ("search", "Search Confluence pages with CQL or text.", {"query": S}, ["query"]),
        ("get_page", "Get a Confluence page by id.", {"page_id": S}, ["page_id"]),
        ("create_page", "Create a Confluence page in a space.", {"space": S, "title": S, "body": S}, ["space", "title"]),
        ("update_page", "Update a Confluence page body.", {"page_id": S, "body": S}, ["page_id", "body"]),
    ]),
    "postgres": ("Production PostgreSQL database (read-only role)", [
        ("run_query", "Run a read-only SQL query on the production Postgres database.", {"sql": S}, ["sql"]),
        ("list_tables", "List tables in the Postgres database.", {"schema": S}, []),
        ("describe_table", "Show columns and types of a Postgres table.", {"table": S}, ["table"]),
        ("explain_query", "Show the Postgres query plan (EXPLAIN) for a SQL statement.", {"sql": S}, ["sql"]),
    ]),
    "bigquery": ("BigQuery analytics warehouse", [
        ("run_query", "Run a SQL query in BigQuery (analytics warehouse).", {"sql": S}, ["sql"]),
        ("list_datasets", "List BigQuery datasets.", {}, []),
        ("get_table_schema", "Get the schema of a BigQuery table.", {"table": "dataset.table"}, ["table"]),
        ("estimate_cost", "Dry-run a BigQuery query to estimate bytes processed and cost.", {"sql": S}, ["sql"]),
    ]),
    "salesforce": ("Salesforce CRM", [
        ("search_accounts", "Search Salesforce accounts.", {"query": S}, ["query"]),
        ("get_opportunity", "Get a Salesforce opportunity.", {"opportunity_id": S}, ["opportunity_id"]),
        ("update_opportunity_stage", "Change the stage of a Salesforce opportunity.", {"opportunity_id": S, "stage": S}, ["opportunity_id", "stage"]),
        ("create_lead", "Create a Salesforce lead.", {"name": S, "company": S, "email": S}, ["name", "company"]),
        ("log_call", "Log a call activity on a Salesforce record.", {"record_id": S, "notes": S}, ["record_id", "notes"]),
    ]),
    "hubspot": ("HubSpot CRM (marketing and SMB sales)", [
        ("search_contacts", "Search HubSpot contacts.", {"query": S}, ["query"]),
        ("create_contact", "Create a HubSpot contact.", {"email": S, "name": S}, ["email"]),
        ("create_deal", "Create a HubSpot deal.", {"name": S, "amount": "number", "stage": S}, ["name"]),
        ("update_deal_stage", "Move a HubSpot deal to a pipeline stage.", {"deal_id": S, "stage": S}, ["deal_id", "stage"]),
        ("log_email", "Log an email engagement on a HubSpot contact.", {"contact_id": S, "subject": S, "body": S}, ["contact_id", "subject"]),
    ]),
    "stripe": ("Stripe payments", [
        ("list_charges", "List Stripe charges, optionally for a customer.", {"customer_id": S, "limit": "integer"}, []),
        ("refund_charge", "Refund a Stripe charge (full or partial).", {"charge_id": S, "amount": "number"}, ["charge_id"]),
        ("get_customer", "Get a Stripe customer by id or email.", {"customer": S}, ["customer"]),
        ("create_invoice", "Create a Stripe invoice for a customer.", {"customer_id": S, "amount": "number", "description": S}, ["customer_id", "amount"]),
        ("cancel_subscription", "Cancel a Stripe subscription.", {"subscription_id": S, "at_period_end": "boolean"}, ["subscription_id"]),
    ]),
    "zendesk": ("Zendesk customer support tickets", [
        ("create_ticket", "Create a Zendesk support ticket.", {"subject": S, "description": S, "requester": S}, ["subject", "description"]),
        ("search_tickets", "Search Zendesk tickets.", {"query": S}, ["query"]),
        ("update_ticket", "Update a Zendesk ticket's status, priority or assignee.", {"ticket_id": "integer", "status": S, "priority": S}, ["ticket_id"]),
        ("add_internal_note", "Add a private internal note to a Zendesk ticket.", {"ticket_id": "integer", "note": S}, ["ticket_id", "note"]),
    ]),
    "datadog": ("Datadog monitoring: metrics, logs, monitors", [
        ("query_metrics", "Query Datadog timeseries metrics.", {"query": S, "from": S, "to": S}, ["query"]),
        ("search_logs", "Search Datadog logs.", {"query": S, "from": S}, ["query"]),
        ("list_monitors", "List Datadog monitors and their states.", {"tag": S}, []),
        ("mute_monitor", "Mute a Datadog monitor for a duration.", {"monitor_id": "integer", "minutes": "integer"}, ["monitor_id"]),
        ("create_incident", "Declare a Datadog incident.", {"title": S, "severity": S}, ["title"]),
    ]),
    "pagerduty": ("PagerDuty on-call and incidents", [
        ("list_incidents", "List PagerDuty incidents.", {"status": S}, []),
        ("acknowledge_incident", "Acknowledge a PagerDuty incident.", {"incident_id": S}, ["incident_id"]),
        ("resolve_incident", "Resolve a PagerDuty incident.", {"incident_id": S}, ["incident_id"]),
        ("get_oncall", "Who is on call now for a PagerDuty schedule or service.", {"schedule": S}, []),
    ]),
    "aws": ("AWS account operations", [
        ("list_ec2_instances", "List EC2 instances in a region.", {"region": S, "state": S}, []),
        ("get_cost_and_usage", "Get AWS cost and usage for a period, grouped by service.", {"start": S, "end": S}, ["start", "end"]),
        ("describe_s3_bucket", "Describe an S3 bucket (size, policy, versioning).", {"bucket": S}, ["bucket"]),
        ("invalidate_cloudfront", "Create a CloudFront cache invalidation for paths.", {"distribution_id": S, "paths": S}, ["distribution_id", "paths"]),
    ]),
    "figma": ("Figma design files", [
        ("get_file", "Get a Figma file's structure.", {"file_key": S}, ["file_key"]),
        ("list_comments", "List comments on a Figma file.", {"file_key": S}, ["file_key"]),
        ("post_comment", "Post a comment on a Figma file.", {"file_key": S, "message": S}, ["file_key", "message"]),
        ("export_frame", "Export a Figma frame as PNG/SVG.", {"file_key": S, "node_id": S, "format": S}, ["file_key", "node_id"]),
    ]),
    "util": ("General utilities", [
        ("get_weather", "Current weather and forecast for a city.", {"city": S}, ["city"]),
        ("geocode", "Convert an address to coordinates.", {"address": S}, ["address"]),
        ("translate_text", "Translate text between languages.", {"text": S, "target_lang": S}, ["text", "target_lang"]),
        ("currency_convert", "Convert an amount between currencies at today's rate.", {"amount": "number", "from": S, "to": S}, ["amount", "from", "to"]),
        ("web_search", "Search the public web.", {"query": S}, ["query"]),
        ("fetch_url", "Fetch a public web page as text.", {"url": S}, ["url"]),
    ]),
}


def schema(server, name, desc, params, required):
    props = {}
    for k, v in params.items():
        t = v if v in ("string", "integer", "number", "boolean") else "string"
        props[k] = {"type": t} | ({} if v == t else {"description": v})
    return {"type": "function", "function": {"name": f"{server}__{name}", "description": desc,
                                             "parameters": {"type": "object", "properties": props, "required": required}}}


CATALOG = [schema(s, *t) for s, (_, tools) in SERVERS.items() for t in tools]
BY_NAME = {t["function"]["name"]: t for t in CATALOG}

# (query, gold tool, {arg: substring that must appear in that arg (case-insensitive)})
# Half Korean, half English; most have a near-duplicate tool on another server.
QUERIES = [
    ("Open a bug in Jira project PAY: 'Refund webhook times out'.", "jira__create_issue", {"project_key": "PAY"}),
    ("Linear의 Platform 팀에 '로그인 페이지 다크모드 깨짐' 이슈 만들어줘.", "linear__create_issue", {"team": "platform"}),
    ("acme/web 레포에 '빌드 캐시 안 됨' 이슈 하나 올려줘.", "github__create_issue", {"repo": "acme/web"}),
    ("In the GitLab project infra/terraform, file an issue titled 'Upgrade provider to v6'.", "gitlab__create_issue", {"project": "infra/terraform"}),
    ("Post 'deploy finished ✅' in the #eng-releases Slack channel.", "slack__send_message", {"channel": "eng-releases"}),
    ("Teams의 Sales 팀 general 채널에 '이번 주 리포트 올렸습니다' 라고 남겨줘.", "teams__send_channel_message", {"team": "sales"}),
    ("슬랙으로 minji에게 '회의록 확인 부탁해요' DM 보내줘.", "slack__send_dm", {"user": "minji"}),
    ("Email jane@acme.com from my Gmail: subject 'Contract', body 'Signed copy attached'.", "gmail__send_email", {"to": "jane@acme.com"}),
    ("회사 Outlook 메일로 cfo@acme.com 에게 '3분기 예산안' 제목으로 메일 보내줘. 본문은 '검토 부탁드립니다'.", "outlook__send_mail", {"to": "cfo@acme.com"}),
    ("Draft (don't send) a Gmail reply to bob@x.com saying I'll be late.", "gmail__create_draft", {"to": "bob@x.com"}),
    ("Put a 'Design review' event on my Google Calendar tomorrow 3-4pm.", "gcal__create_event", {"title": "design review"}),
    ("Outlook 캘린더에 금요일 10시~11시 '1:1 with Kim' 일정 추가해줘.", "outlook__create_event", {"title": "1:1"}),
    ("민지, 철수, 영희 셋이 다음 주에 30분 같이 비는 시간 찾아줘 (구글 캘린더 기준).", "gcal__find_free_time", {"duration_min": "30"}),
    ("Schedule a Teams meeting 'Vendor sync' with ops@acme.com on 2026-11-03 14:00.", "teams__schedule_meeting", {"title": "vendor sync"}),
    ("구글 드라이브에서 '온보딩 체크리스트' 문서 찾아줘.", "gdrive__search_files", {"query": "온보딩"}),
    ("Search our Notion for the 'incident postmortem template'.", "notion__search", {"query": "postmortem"}),
    ("Confluence에서 'VPN 설정 가이드' 페이지 검색해줘.", "confluence__search", {"query": "vpn"}),
    ("How many orders were placed yesterday? Check the production Postgres orders table.", "postgres__run_query", {"sql": "orders"}),
    ("BigQuery의 analytics.events 테이블에서 지난주 DAU 쿼리 돌려줘.", "bigquery__run_query", {"sql": "events"}),
    ("Before running it, estimate how much this BigQuery query would cost: SELECT * FROM logs.raw", "bigquery__estimate_cost", {"sql": "logs.raw"}),
    ("Postgres users 테이블 컬럼 구조 보여줘.", "postgres__describe_table", {"table": "users"}),
    ("Move Salesforce opportunity 006XYZ to 'Closed Won'.", "salesforce__update_opportunity_stage", {"opportunity_id": "006XYZ", "stage": "closed won"}),
    ("HubSpot 딜 8812를 'contract sent' 단계로 옮겨줘.", "hubspot__update_deal_stage", {"deal_id": "8812"}),
    ("Add a HubSpot contact for lee@startup.io named Lee.", "hubspot__create_contact", {"email": "lee@startup.io"}),
    ("Stripe 결제 ch_3Abc 전액 환불해줘.", "stripe__refund_charge", {"charge_id": "ch_3abc"}),
    ("Cancel Stripe subscription sub_778 at the end of the billing period.", "stripe__cancel_subscription", {"subscription_id": "sub_778"}),
    ("젠데스크 티켓 4521에 '고객에게 환불 안내함' 내부 메모 남겨줘 (고객은 못 보게).", "zendesk__add_internal_note", {"ticket_id": "4521"}),
    ("Who is on call right now for the payments PagerDuty schedule?", "pagerduty__get_oncall", {}),
    ("Datadog에서 최근 1시간 checkout 서비스 에러 로그 찾아줘.", "datadog__search_logs", {"query": "checkout"}),
    ("Mute Datadog monitor 99123 for 60 minutes.", "datadog__mute_monitor", {"monitor_id": "99123"}),
    ("Acknowledge PagerDuty incident PXK12.", "pagerduty__acknowledge_incident", {"incident_id": "PXK12"}),
    ("지난달 AWS 비용 서비스별로 알려줘.", "aws__get_cost_and_usage", {}),
    ("CloudFront 배포 E2ABC의 /static/* 캐시 무효화해줘.", "aws__invalidate_cloudfront", {"distribution_id": "E2ABC"}),
    ("Retry the failed GitLab pipeline 5531 in infra/terraform.", "gitlab__retry_pipeline", {"pipeline_id": "5531"}),
    ("Merge PR #42 in acme/api with squash.", "github__merge_pull_request", {"repo": "acme/api"}),
    ("GitLab group/app 에 feature/login → main 머지 리퀘스트 열어줘.", "gitlab__create_merge_request", {"project": "group/app"}),
    ("Jira PAY-311 을 Done 으로 넘겨줘.", "jira__transition_issue", {"issue_key": "PAY-311"}),
    ("Leave a comment on Figma file k3yF: 'Button contrast too low'.", "figma__post_comment", {"file_key": "k3yF"}),
    ("100달러가 원화로 얼마야?", "util__currency_convert", {}),
    ("Share the Google Doc 1AbC with intern@acme.com as a viewer.", "gdrive__share_file", {"file_id": "1AbC"}),
]


# ---------- round 2: "realistic" catalog ----------
# Real MCP servers ship long descriptions (usage notes, params, return shape, errors) and companies connect several
# instances of the same product. Both make the all-tools prompt big and the choice harder.
INSTANCES = {  # base server -> (clone name, clone summary, base summary override)
    "github": ("github_oss", "GitHub — public open-source org 'acme-oss' (public repos)", "GitHub — company org 'acme' (private repos)"),
    "slack": ("slack_community", "Slack — public community workspace for users", "Slack — internal company workspace"),
    "jira": ("jira_legacy", "Jira Server — legacy on-prem instance (projects before 2023, read-mostly)", "Jira Cloud — current projects"),
}


def verbose(server, summary, name, desc, params, required):
    ex = {k: ("acme/web" if "owner" in str(v) else 1 if v in ("integer", "number") else "...") for k, v in params.items()}
    plines = "\n".join(f"- {k} ({'required' if k in required else 'optional'}): "
                       f"{v if v not in ('string', 'integer', 'number', 'boolean') else 'value of type ' + v}. "
                       f"Must refer to an object in this {summary.split(' —')[0]} connection." for k, v in params.items())
    return (f"[{summary}] {desc}\n\nWhen to use: use this tool only for {summary}. Other connected services may offer a "
            f"similarly named tool; pick the one that matches where the user's data lives.\n\nParameters:\n{plines or '- none'}"
            f"\n\nReturns: a JSON object with `id`, `url`, `created_at`, `updated_at`, `status` and the full resource. "
            f"List-style results are paginated (max 100 per page; pass the returned `cursor` to continue).\n\n"
            f"Errors: 401 when the connection token expired (ask the user to reconnect in Settings > Connectors), "
            f"403 when the connected account lacks permission, 404 when the object does not exist, 429 when rate "
            f"limited (retry after `retry_after` seconds). Timestamps are ISO-8601 UTC.\n\n"
            f"Permissions: runs with the permissions of the user's connected account; actions are audited.\n\n"
            f"Example: {server}__{name}({json.dumps(ex)})")


def realistic():
    servers = dict(SERVERS)
    for base, (clone, clone_sum, base_sum) in INSTANCES.items():
        servers[base] = (base_sum, SERVERS[base][1])
        servers[clone] = (clone_sum, SERVERS[base][1])
    catalog = []
    for s, (summary, tools) in servers.items():
        for name, desc, params, req in tools:
            t = schema(s, name, desc, params, req)
            t["function"]["description"] = verbose(s, summary, name, desc, params, req)
            catalog.append(t)
    return servers, catalog


QUERIES_MULTI = [
    ("커뮤니티 슬랙 #announcements 채널에 'v2.0 released' 공지 올려줘.", "slack_community__send_message", {"channel": "announcements"}),
    ("Post 'standup in 5' to #team-core in our internal Slack.", "slack__send_message", {"channel": "team-core"}),
    ("Open an issue in acme-oss/sdk (our public OSS repo): 'Typo in README'.", "github_oss__create_issue", {"repo": "acme-oss/sdk"}),
    ("acme/billing (사내 private 레포)의 열린 PR 목록 보여줘.", "github__list_pull_requests", {"repo": "acme/billing"}),
    ("레거시 Jira 서버에 있는 OLD-77 이슈 내용 보여줘 (2022년 프로젝트).", "jira_legacy__get_issue", {"issue_key": "OLD-77"}),
    ("Comment 'fixed in 3.2' on Jira Cloud issue PAY-12.", "jira__add_comment", {"issue_key": "PAY-12"}),
    ("Search the community Slack for messages about 'rate limit'.", "slack_community__search_messages", {"query": "rate limit"}),
    ("공개 OSS 레포 acme-oss/cli 에 v1.4.0 릴리스 만들어줘.", "github_oss__create_release", {"repo": "acme-oss/cli"}),
]
