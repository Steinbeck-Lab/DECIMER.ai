"""Run by the `glitchtip` service BEFORE its web server starts (compose.yaml: `manage.py shell < this file`).

GlitchTip lets anybody register while no account exists (`ENABLE_USER_REGISTRATION or not User.objects.exists()`),
which would make the first minutes of a fresh GlitchTip a race against whoever else is on its networks. So the
first account is made here, before anything listens, and everything the UI would need is made with it. Safe to
run on every start: it creates nothing that exists, never adds an account to a GlitchTip that has one, and never
changes a password.

  * the superuser GLITCHTIP_ADMIN_EMAIL with GLITCHTIP_ADMIN_PASSWORD (deploy/.env), only when there is no user at all;
  * the organisation `decimer` and the projects `decimer-api` (the api and the workers) and `decimer-web` (the
    browsers), and a team that holds both;
  * "scrub IP addresses" on for the organisation and every project (GlitchTip then anonymises any address it is
    given; the edge gives it none, see edge.conf);
  * and one line per project telling which DSN to put into deploy/.env: `$DC logs glitchtip | grep DSN`.

If anything here fails the service stops (it does not fall through to the web server): a GlitchTip with an open
registration must not come up.
"""

import os

from allauth.account.models import EmailAddress
from apps.organizations_ext.models import Organization
from apps.projects.models import Project, ProjectKey
from apps.teams.models import Team
from apps.users.models import User

email = os.environ["GLITCHTIP_ADMIN_EMAIL"].strip()
password = os.environ["GLITCHTIP_ADMIN_PASSWORD"]
if not email or not password:
    raise SystemExit("glitchtip-init: GLITCHTIP_ADMIN_EMAIL and GLITCHTIP_ADMIN_PASSWORD must be set in deploy/.env")

user = User.objects.filter(email=email).first()
if user is None and not User.objects.exists():
    user = User.objects.create_superuser(email=email, password=password)
    print(f"glitchtip-init: created the account {email}")
if user is not None:
    EmailAddress.objects.get_or_create(user=user, email=user.email, defaults={"primary": True, "verified": True})

organization, _ = Organization.objects.get_or_create(slug="decimer", defaults={"name": "decimer"})
organization.scrub_ip_addresses = True
organization.save(update_fields=["scrub_ip_addresses"])
if user is not None and not organization.users.filter(pk=user.pk).exists():
    organization.add_user(user)

for slug, platform in (("decimer-api", "python"), ("decimer-web", "javascript")):
    project, _ = Project.objects.get_or_create(
        slug=slug, organization=organization, defaults={"name": slug, "platform": platform}
    )
    if not project.scrub_ip_addresses:
        project.scrub_ip_addresses = True
        project.save(update_fields=["scrub_ip_addresses"])
    team, _ = Team.objects.get_or_create(slug="team", organization=organization, defaults={})
    if user is not None:
        team.members.add(organization.organization_users.get(user=user))
    team.projects.add(project)
    key = ProjectKey.objects.filter(project=project).first() or ProjectKey.objects.create(project=project)
    host = "glitchtip-gate:8000" if slug == "decimer-api" else "<the site's host>/glitchtip"
    name = "SENTRY_DSN" if slug == "decimer-api" else "VITE_SENTRY_DSN (and GLITCHTIP_WEB_PROJECT_ID)"
    print(f"glitchtip-init: DSN {slug}: project id {project.id}, key {key.public_key.hex}: {name}=<scheme>://{key.public_key.hex}@{host}/{project.id}")
