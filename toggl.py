#!/usr/bin/env python

import sys
import json
import urllib
import logging
import time


class TogglException(IOError):
    """ Custom exception to indicate Toggl API errors"""
    pass


class TogglRateLimitException(TogglException):
    pass


class Toggl(object):
    """ Class to access Toggl API

    API docs can be found at:
    https://github.com/toggl/toggl_api_docs/blob/master/reports.md
    """
    baseURL = 'toggl.com'
    date_format = '%Y-%m-%d'  # YYYY-MM-DD
    connection = None
    # rate limit settings
    _rate_limit_pause = 0  # it's adaptive. Don't change at runtime
    retries = 3
    # rate caching
    cache = True
    _cache = None

    def __init__(self, api_token, cache=True):
        if sys.version_info > (3,):  # Python 2/3 compatibility
            import http
            self.connection = http.client.HTTPSConnection(self.baseURL)
        else:
            import httplib
            self.connection = httplib.HTTPSConnection(self.baseURL)

        self.logger = logging.getLogger(__name__)
        self.auth_header = {'Authorization': "Basic %s" %
                            (api_token + ':api_token').encode("base64").rstrip()}
        self.cache = cache
        self.flush()

    def flush(self):
        self._cache = {}

    def _get_json(self, url, method='GET', body=None):
        self.logger.debug("_get_json: url=%s, method=%s, body=%s" %
                          (url, method, body))
        # Caching
        if self.cache and method == 'GET' and url in self._cache:
            self.logger.debug("Cache hit! returning from cache: %s" %
                              json.dumps(self._cache[url]))
            return self._cache[url]

        self.connection.request(method, url, body, self.auth_header)
        try:
            response = self.connection.getresponse()
        except:
            self.logger.error('Failed to open url: %s' % url)
            raise
        response_text = response.read()

        if response.status == 429:
            self.logger.debug("Hit API request rate limit, pause for 2 sec")
            raise TogglRateLimitException("Status 429 returned by Toggl API")

        elif response.status > 200:
            raise TogglException(
                "API call %s returned status %s. The response was:\n %s" %
                (url, response.status, response_text))

        self.logger.debug("Response from Toggl: %s" % response_text)
        response_json = json.loads(response_text)
        if 'error' in response_json:
            raise TogglException("""Error getting Toggl data:
            message: %(message)s
            tip: %(tip)s
            code: %(code)s""" % response_json['error'])

        if self.cache and method == 'GET':
            self._cache[url] = response_json

        return response_json

    def _request(self, api_func, params=None, body=None, method='GET',
                 filters=None):
        """  Internal method to call Toggl API
        :param api_func: url of the API function without the hostname
        :param params: query string params (aka GET params)
        :param body: If body present, a POST request is issued
        :param filters: filters applied if the returned object is a list of
               dicts
        :return: arbitrary object or a list of objects retured by the specified
                API function and filtered with the specified filters
        """
        query = '' if params is None else '?' + urllib.urlencode(params)
        url = api_func + query
        if body is not None and method == 'GET':
            method = 'POST'

        for i in range(self.retries):
            try:
                response = self._get_json(url, method, body=body)
            except TogglRateLimitException as e:
                if i == self.retries - 1:
                    raise e
                self._rate_limit_pause += 1
                time.sleep(self._rate_limit_pause)
            else:
                self._rate_limit_pause = max(0, self._rate_limit_pause - 1)
                break

        if filters is None:
            return response
        return [i for i in response
                if all(i.get(k) == v for k, v in filters.items())]

    def get_workspaces(self, **filters):
        """ Get the list of workspaces
        :returns 2-tuple list of workspaces: [(<name>, <id>), ...]. Example of
            workspace data fields: {
                'id': 1860000,
                'name': 'test workspace'
                'profile': 0,
                'premium': False,
                'admin': True,
                'default_hourly_rate': 0,
                'default_currency': 'USD',
                'only_admins_may_create_projects": False,
                'only_admins_see_billable_rates": False,
                'only_admins_see_team_dashboard": False,
                'project_billable_by_default": True,
                'rounding': 1,
                'api_token': None,
                'at': "2014-08-28T10:00:00+00:00"
                'ical_enabled': True,
                'subscription': {'subscription_id': 0, ...}
            }

        https://github.com/toggl/toggl_api_docs/blob/master/chapters/workspaces.md#get-workspaces
        """

        data = self._request('/api/v8/workspaces', filters=filters)

        # filter out personal workspaces:
        return [w for w in data
                if 'personal' not in w['name'] and w['admin']]

    def get_workspace_users(self, workspace_id, **filters):
        """ Get the list of workspace users.
        :param workspace_id: toggle workspace id, obtained from get_workspaces
        :return: list of user dicts

        Example: Get list of active users:
            get_workspace_users(ws_id, active=True)

        https://github.com/toggl/toggl_api_docs/blob/master/chapters/workspace_users.md
        """
        return [u for u in self._request(
            '/api/v8/workspaces/{0}/workspace_users'.format(wid),
            filters=filters)]

    def get_projects(self, wid, **filters):
        """ Get projects information
        :param wid: toggle workspace id, obtained from get_workspaces
        :return: list of active project dicts. Example: [
            {   "id":909,
                "wid":777,
                "cid":987,
                "name":"Very lucrative project",
                "billable":false,
                "is_private":true,
                "active":true,
                "at":"2013-03-06T09:15:18+00:00" },
            ... ]

        https://github.com/toggl/toggl_api_docs/blob/master/chapters/workspaces.md#get-workspace-projects
        """
        return self._request(
            '/api/v8/workspaces/{0}/projects'.format(wid),
            filters=filters)

    def weekly_report(self, workspace_id, since, until):
        """ Toggl weekly report for a given team """
        return self._request('/reports/api/v2/weekly', {
            'workspace_id': wid,
            'since': since.strftime(self.date_format),
            'until': until.strftime(self.date_format),
            'user_agent': 'github.com/user2589/Toggl.py',
            'order_field': 'title',
            # title/day1/day2/day3/day4/day5/day6/day7/week_total
            'display_hours': 'decimal',  # decimal/minutes
        })

    def detailed_report(self, wid, since, until):
        """ Toggl detailed report for a given team

        https://github.com/toggl/toggl_api_docs/blob/master/reports/detailed.md#example
        """
        page = 1
        records_read = 0
        records = []
        while True:
            report_page = self._request('/reports/api/v2/details', {
                'workspace_id': wid,
                'since': since.strftime(self.date_format),
                'until': until.strftime(self.date_format),
                'user_agent': 'github.com/user2589/Toggl.py',
                'order_field': 'date',
                # date/description/duration/user in detailed reports
                'order_desc': 'off',  # on/off
                'display_hours': 'decimal',  # decimal/minutes
                'page': page
            })
            records.extend(report_page['data'])

            records_read += report_page['per_page']
            page += 1

            if records_read >= report_page['total_count']:
                return records
