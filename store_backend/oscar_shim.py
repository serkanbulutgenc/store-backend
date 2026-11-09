"""Keeps Oscar's `dashboard` app label registered without the dashboard.

`oscar.config.Shop.ready()` and the `dashboard_tags` templatetag module need the
`dashboard` app label. The real `DashboardConfig.ready()` loads the dashboard's
navigation and sub-apps, which this project does not install, so it is a no-op here.
"""

from oscar.apps.dashboard.apps import DashboardConfig as OscarDashboardConfig


class DashboardConfig(OscarDashboardConfig):
    def ready(self) -> None:
        pass
