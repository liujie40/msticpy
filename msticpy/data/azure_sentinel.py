# -------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for
# license information.
# --------------------------------------------------------------------------
"""Uses the Azure Python SDK to collect and return details related to Azure."""
from typing import Dict, List, Tuple, Optional, Union
from uuid import uuid4
from collections import Counter

import pandas as pd
from pandas.core.base import NoNewAttributesMixin
import requests
from azure.common.exceptions import CloudError
from uuid import uuid4, UUID
from IPython.core.display import display

from .azure_data import AzureData
from ..common.azure_auth_core import AzCredentials, AzureCloudConfig
from ..common.exceptions import MsticpyAzureConfigError, MsticpyUserError
from ..common.wsconfig import WorkspaceConfig

_PATH_MAPPING = {
    "ops_path": "/providers/Microsoft.SecurityInsights/operations",
    "alert_rules": "/providers/Microsoft.SecurityInsights/alertRules",
    "ss_path": "/savedSearches",
    "bookmarks": "/providers/Microsoft.SecurityInsights/bookmarks",
    "incidents": "/providers/Microsoft.SecurityInsights/incidents",
    "data_connectors": "/providers/Microsoft.SecurityInsights/dataConnectors",
    "watchlists": "/providers/Microsoft.SecurityInsights/watchlists",
    "alert_template": "/providers/Microsoft.SecurityInsights/alertRuleTemplates",
}


class AzureSentinel(AzureData):
    """Class for returning key Microsoft Sentinel elements."""

    def __init__(
        self,
        connect: bool = False,
        cloud: Optional[str] = None,
        res_id: Optional[str] = None,
    ):
        """
        Initialize connector for Azure APIs.

        Parameters
        ----------
        connect : bool, optional
            Set true if you want to connect to API on initialization, by default False
        cloud : str, optional
            Specify cloud to use, overriding any configuration value.
            Default is to use configuration setting or public cloud if no
            configuration setting is available.
        res_id : str, optional
            Set the Sentinel workspace resource ID you want to use, if not specified
            defaults will be looked for or details can be passed seperately with functions.

        """
        super().__init__(connect=connect, cloud=cloud)
        self.config = None
        self.base_url = self.endpoints.resource_manager
        self.default_subscription: Optional[str] = None
        self.default_workspace: Optional[Tuple[str, str]] = None
        self.res_id = _validate_res_id(res_id)

    def connect(self, auth_methods: List = None, silent: bool = False, **kwargs):
        """
        Authenticate with the SDK & API.

        Parameters
        ----------
        auth_methods : List, optional
            list of preferred authentication methods to use, by default None
        silent : bool, optional
            Set true to prevent output during auth process, by default False

        """
        super().connect(auth_methods=auth_methods, silent=silent)
        if "token" in kwargs:
            self.token = kwargs["token"]
        else:
            self.token = _get_token(self.credentials)  # type: ignore

        self.res_group_url = None
        self.prov_path = None

    def set_default_subscription(self, subscription_id: str):
        """Set the default subscription to use to `subscription_id`."""
        subs_df = self.get_subscriptions()
        if subscription_id in subs_df["Subscription ID"].values:
            self.default_subscription = subscription_id
        else:
            print(f"Subscription ID {subscription_id} not found.")
            print(
                f"Subscriptions found: {', '.join(subs_df['Subscription ID'].values)}"
            )

    def list_sentinel_workspaces(self, sub_id: str = None) -> Dict[str, str]:
        """
        Return a list of Microsoft Sentinel workspaces in a Subscription.

        Parameters
        ----------
        sub_id : str
            The subscription ID to get a list of workspaces from.
            If not provided it will attempt to get sub_id from config files.

        Returns
        -------
        Dict
            A dictionary of workspace names and ids

        """
        # If a subscription ID isn't provided try and get one from config files.
        sub_id = sub_id or self.default_subscription
        if not sub_id:
            config = self._check_config(["subscription_id"])
            sub_id = config["subscription_id"]

        print("Finding Microsoft Sentinel Workspaces...")
        res = self.get_resources(sub_id=sub_id)  # type: ignore
        # handle no results
        if isinstance(res, pd.DataFrame) and not res.empty:
            sentinel = res[
                (res["resource_type"] == "Microsoft.OperationsManagement/solutions")
                & (res["name"].str.startswith("SecurityInsights"))
            ]
            workspaces = []
            for wrkspace in sentinel["resource_id"]:
                res_details = self.get_resource_details(
                    sub_id=sub_id, resource_id=wrkspace  # type: ignore
                )
                workspaces.append(res_details["properties"]["workspaceResourceId"])

            workspaces_dict = {}
            for wrkspace in workspaces:
                name = wrkspace.split("/")[-1]
                workspaces_dict[name] = wrkspace
            return workspaces_dict

        print(f"No Microsoft Sentinel workspaces in {sub_id}")
        return {}

    def set_default_workspace(
        self, sub_id: Optional[str], workspace: Optional[str] = None
    ):
        """
        Set the default workspace.

        Parameters
        ----------
        sub_id : Optional[str], optional
            Subscription ID containing the workspace. If not specified,
            the subscription will be taken from the `default_subscription`
            or from configuration.
        workspace : Optional[str], optional
            Name of the workspace, by default None.
            If not specified and there is only one workspace in the
            subscription, this will be set as the default.

        """
        sub_id = sub_id or self.default_subscription
        workspaces = self.get_sentinel_workspaces(sub_id=sub_id)
        if len(workspaces) == 1:
            self.default_workspace = next(iter(workspaces.items()))
        elif workspace in workspaces:
            self.default_workspace = workspace, workspaces[workspace]

    def _get_default_workspace(self):
        """Return the default workspace ResourceID."""
        if self.default_workspace:
            return self.default_workspace[0]
        return None

    def list_hunting_queries(
        self,
        res_id: str = None,
        sub_id: str = None,
        res_grp: str = None,
        ws_name: str = None,
    ) -> pd.DataFrame:
        """
        Return all hunting queries in a Microsoft Sentinel workspace.

        Parameters
        ----------
        res_id : str, optional
            Resource ID of the workspace, if not provided details from config file will be used.
        sub_id : str, optional
            Sub ID of the workspace, to be used if not providing Resource ID.
        res_grp : str, optional
            Resource Group name of the workspace, to be used if not providing Resource ID.
        ws_name : str, optional
            Workspace name of the workspace, to be used if not providing Resource ID.

        Returns
        -------
        pd.DataFrame
            A table of the hunting queries.

        """
        saved_query_df = self._list_items(
            item_tpye="alert_rules",
            api_version="2017-04-26-preview",
            res_id=res_id,
            sub_id=sub_id,
            res_grp=res_grp,
            ws_name=ws_name,
        )
        return saved_query_df[
            saved_query_df["properties.Category"] == "Hunting Queries"
        ]

    def list_alert_rules(
        self,
        res_id: str = None,
        sub_id: str = None,
        res_grp: str = None,
        ws_name: str = None,
    ) -> pd.DataFrame:
        """
        Return all Microsoft Sentinel alert rules for a workspace.

        Parameters
        ----------
        res_id : str, optional
            Resource ID of the workspace, if not provided details from config file will be used.
        sub_id : str, optional
            Sub ID of the workspace, to be used if not providing Resource ID.
        res_grp : str, optional
            Resource Group name of the workspace, to be used if not providing Resource ID.
        ws_name : str, optional
            Workspace name of the workspace, to be used if not providing Resource ID.

        Returns
        -------
        pd.DataFrame
            A table of the workspace's alert rules.

        """
        return self._list_items(
            item_tpye="alert_rules",
            res_id=res_id,
            sub_id=sub_id,
            res_grp=res_grp,
            ws_name=ws_name,
        )

    def list_bookmarks(
        self,
        res_id: str = None,
        sub_id: str = None,
        res_grp: str = None,
        ws_name: str = None,
    ) -> pd.DataFrame:
        """
        Return a list of Bookmarks from a Sentinel workspace.

        Parameters
        ----------
        res_id : str, optional
            Resource ID of the workspace, if not provided details from config file will be used.
        sub_id : str, optional
            Sub ID of the workspace, to be used if not providing Resource ID.
        res_grp : str, optional
            Resource Group name of the workspace, to be used if not providing Resource ID.
        ws_name : str, optional
            Workspace name of the workspace, to be used if not providing Resource ID.

        Returns
        -------
        pd.DataFrame
            A set of bookmarks.

        Raises
        ------
        CloudError
            If bookmark collection fails.

        """
        return self._list_items(
            item_tpye="bookmarks",
            res_id=res_id,
            sub_id=sub_id,
            res_grp=res_grp,
            ws_name=ws_name,
        )

    # ToDo get results section working
    def create_bookmark(
        self,
        name: str,
        query: str,
        results: str = None,
        notes: str = None,
        labels: List[str] = None,
        res_id: str = None,
        sub_id: str = None,
        res_grp: str = None,
        ws_name: str = None,
    ):
        """Create a bookmark in the Sentinel Workpsace

        Parameters
        ----------
        name : str
            The name of the bookmark to use
        query : str
            The KQL query for the bookmark
        results : str, optional
            The results of the query to include with the bookmark
        notes : str, optional
            Any notes you want associated with the bookmark, by default None
        labels : List[str], optional
            Any labels you want associated with the bookmark, by default None
        res_id : str, optional
            Resource ID of the workspace, if not provided details from config file will be used.
        sub_id : str, optional
            Sub ID of the workspace, to be used if not providing Resource ID.
        res_grp : str, optional
            Resource Group name of the workspace, to be used if not providing Resource ID.
        ws_name : str, optional
            Workspace name of the workspace, to be used if not providing Resource ID.

        Raises
        ------
        CloudError
            If API retunrs an error.

        """
        # Generate or use resource ID
        res_id = res_id or self.res_id or self._get_default_workspace()
        if not res_id:
            res_id = self._build_res_id(sub_id, res_grp, ws_name)
        res_id = _validate_res_id(res_id)
        bkmark_id = str(uuid4())
        url = self._build_paths(res_id, self.base_url)
        bookmark_url = url + _PATH_MAPPING["bookmarks"] + f"/{bkmark_id}"
        data_items = {
            "displayName": name,
            "query": query,
        }
        if results:
            data_items["queryResults"] = results
        if notes:
            data_items["notes"] = notes
        if labels:
            data_items["labels"] = labels
        data = _build_data(data_items, props=True)
        params = {"api-version": "2020-01-01"}
        response = requests.put(
            bookmark_url,
            headers=_get_api_headers(self.token),
            params=params,
            data=str(data),
        )
        if response.status_code == 200:
            print("Bookmark created.")
        else:
            raise CloudError(response=response)

    def delete_bookmark(
        self,
        bookmark: str = None,
        res_id: str = None,
        sub_id: str = None,
        res_grp: str = None,
        ws_name: str = None,
    ):
        """Delete the selected bookmark

        Parameters
        ----------
        bookmark_id : str, optional
            The GUID of the bookmark to delete.
        bookmark_name: str, optional
            The name of the bookmark to delete.
        res_id : str, optional
            Resource ID of the workspace, if not provided details from config file will be used.
        sub_id : str, optional
            Sub ID of the workspace, to be used if not providing Resource ID.
        res_grp : str, optional
            Resource Group name of the workspace, to be used if not providing Resource ID.
        ws_name : str, optional
            Workspace name of the workspace, to be used if not providing Resource ID.

        Raises
        ------
        CloudError
            If the API returns an error.
        """
        res_id = res_id or self.res_id or self._get_default_workspace()
        if not res_id:
            res_id = self._build_res_id(sub_id, res_grp, ws_name)
        res_id = _validate_res_id(res_id)
        bookmark_id = self._get_bookmark_id(bookmark, res_id)
        url = self._build_paths(res_id, self.base_url)
        bookmark_url = url + _PATH_MAPPING["bookmarks"] + f"/{bookmark_id}"
        params = {"api-version": "2020-01-01"}
        response = requests.delete(
            bookmark_url,
            headers=_get_api_headers(self.token),
            params=params,
        )
        if response.status_code == 200:
            print("Bookmark deleted.")
        else:
            raise CloudError(response=response)

    def list_incidents(
        self,
        res_id: str = None,
        sub_id: str = None,
        res_grp: str = None,
        ws_name: str = None,
    ) -> pd.DataFrame:
        """
        Get a list of incident for a Sentinel workspace.

        Parameters
        ----------
        res_id : str, optional
            Resource ID of the workspace, if not provided details from config file will be used.
        sub_id : str, optional
            Sub ID of the workspace, to be used if not providing Resource ID.
        res_grp : str, optional
            Resource Group name of the workspace, to be used if not providing Resource ID.
        ws_name : str, optional
            Workspace name of the workspace, to be used if not providing Resource ID.

        Returns
        -------
        pd.DataFrame
            A table of incidents.

        Raises
        ------
        CloudError
            If incidents could not be retrieved.

        """
        return self._list_items(
            item_tpye="incidents",
            res_id=res_id,
            sub_id=sub_id,
            res_grp=res_grp,
            ws_name=ws_name,
        )

    def get_incident(  # pylint: disable=too-many-locals, too-many-arguments
        self,
        incident: str,
        res_id: str = None,
        sub_id: str = None,
        res_grp: str = None,
        ws_name: str = None,
        entities: bool = False,
        alerts: bool = False,
        comments: bool = False,
        bookmarks: bool = False,
    ) -> pd.DataFrame:
        """
        Get details on a specific incident.

        Parameters
        ----------
        incident_id : str
            Incident ID GUID.
        res_id : str, optional
            Resource ID of the workspace, if not provided details from config file will be used.
        sub_id : str, optional
            Sub ID of the workspace, to be used if not providing Resource ID.
        res_grp : str, optional
            Resource Group name of the workspace, to be used if not providing Resource ID.
        ws_name : str, optional
            Workspace name of the workspace, to be used if not providing Resource ID.
        entities : bool, optional
            If True, include all entities in the response. Default is False.
        alerts : bool, optional
            If True, include all alerts in the response. Default is False.

        Returns
        -------
        pd.DataFrame
            Table containing incident details.

        Raises
        ------
        CloudError
            If incident could not be retrieved.

        """
        res_id = res_id or self.res_id or self._get_default_workspace()
        if not res_id:
            res_id = self._build_res_id(sub_id, res_grp, ws_name)
        res_id = _validate_res_id(res_id)
        incident_id = self._get_incident_id(incident, res_id)
        url = self._build_paths(res_id, self.base_url)
        incidents_url = url + _PATH_MAPPING["incidents"]
        incident_url = incidents_url + f"/{incident_id}"
        params = {"api-version": "2020-01-01"}
        response = requests.get(
            incident_url, headers=_get_api_headers(self.token), params=params
        )
        if response.status_code == 200:
            incident_df = _azs_api_result_to_df(response)
        else:
            raise CloudError(response=response)

        if entities:
            entities_url = incident_url + "/entities"
            ent_parameters = {"api-version": "2019-01-01-preview"}
            ents = requests.post(
                entities_url,
                headers=_get_api_headers(self.token),
                params=ent_parameters,
            )
            if ents.status_code == 200:
                unique_entities = [
                    (ent["kind"], ent["properties"]) for ent in ents.json()["entities"]
                ]
                incident_df["Entities"] = [unique_entities]

        if alerts:
            alerts_url = incident_url + "/alerts"
            alerts_parameters = {"api-version": "2021-04-01"}
            alerts_resp = requests.post(
                alerts_url,
                headers=_get_api_headers(self.token),
                params=alerts_parameters,
            )
            if alerts_resp.status_code == 200:
                for alrts in alerts_resp.json()["value"]:
                    unique_alerts = [
                        {
                            "ID": alrts["properties"]["systemAlertId"],
                            "Name": alrts["properties"]["alertDisplayName"],
                        }
                        for alrts in alerts_resp.json()["value"]
                    ]
                    incident_df["Alerts"] = [unique_alerts]

        if comments:
            comments_url = incident_url + "/comments"
            comment_params = {"api-version": "2021-04-01"}
            comments_response = requests.get(
                comments_url,
                headers=_get_api_headers(self.token),
                params=comment_params,
            )
            if comments_response.status_code == 200:
                comment_details = comments_response.json()
                comments_list = [
                    {
                        "Message": comment["properties"]["message"],
                        "Author": comment["properties"]["author"]["name"],
                    }
                    for comment in comment_details["value"]
                ]
                incident_df["Comments"] = [comments_list]

        if bookmarks:
            relations_url = incident_url + "/relations"
            relations_params = {"api-version": "2021-04-01"}
            relations_response = requests.get(
                relations_url,
                headers=_get_api_headers(self.token),
                params=relations_params,
            )
            bookmarks_list = []
            if relations_response.json()["value"]:

                for relationship in relations_response.json()["value"]:
                    if (
                        relationship["properties"]["relatedResourceType"]
                        == "Microsoft.SecurityInsights/Bookmarks"
                    ):
                        bkmark_id = relationship["properties"]["relatedResourceName"]
                        bookmarks = self.list_bookmarks(res_id)
                        bookmark = bookmarks[bookmarks["name"] == bkmark_id].iloc[0]
                        bookmarks_list.append(
                            {
                                "Bookmark ID": bkmark_id,
                                "Bookmark Title": bookmark["properties.displayName"],
                            }
                        )
            incident_df["Bookmarks"] = [bookmarks_list]

        return incident_df

    def update_incident(
        self,
        incident_id: str,
        update_items: dict,
        res_id: str = None,
        sub_id: str = None,
        res_grp: str = None,
        ws_name: str = None,
    ):
        """
        Update properties of an incident.

        Parameters
        ----------
        incident_id : str
            Incident ID GUID.
        update_items : dict
            Dictionary of properties to update and their values.
            Ref: https://docs.microsoft.com/en-us/rest/api/securityinsights/incidents/createorupdate
        res_id : str, optional
            Resource ID of the workspace, if not provided details from config file will be used.
        sub_id : str, optional
            Sub ID of the workspace, to be used if not providing Resource ID.
        res_grp : str, optional
            Resource Group name of the workspace, to be used if not providing Resource ID.
        ws_name : str, optional
            Workspace name of the workspace, to be used if not providing Resource ID.

        Raises
        ------
        CloudError
            If incident could not be updated.

        """
        res_id = res_id or self.res_id or self._get_default_workspace()
        if not res_id:
            res_id = self._build_res_id(sub_id, res_grp, ws_name)
        res_id = _validate_res_id(res_id)

        incident_dets = self.get_incident(incident_id=incident_id, res_id=res_id)
        url = self._build_paths(res_id, self.base_url)
        incidents_url = url + _PATH_MAPPING["incidents"]
        incident_url = incidents_url + f"/{incident_id}"
        params = {"api-version": "2020-01-01"}
        if "title" not in update_items.keys():
            update_items["title"] = incident_dets.iloc[0]["properties.title"]
        if "status" not in update_items.keys():
            update_items["status"] = incident_dets.iloc[0]["properties.status"]
        data = _build_data(update_items, etag=incident_dets.iloc[0]["etag"])
        response = requests.put(
            incident_url,
            headers=_get_api_headers(self.token),
            params=params,
            data=str(data),
        )
        if response.status_code == 200:
            print("Incident updated.")
        else:
            raise CloudError(response=response)

    def create_incident(
        self,
        title: str,
        severity: str,
        status: str = "New",
        description: str = None,
        first_activity_time: str = None,
        last_activity_time: str = None,
        labels: List = None,
        bookmarks: List = None,
        res_id: str = None,
        sub_id: str = None,
        res_grp: str = None,
        ws_name: str = None,
    ):
        """Create a Sentinel Incident

        Parameters
        ----------
        title : str
            The title of the incident to create
        severity : str
            The severity to assign the incident, options are:
               Informational, Low, Medium, High
        status : str, optional
            The status to assign the incident, by default "New"
            Options are:
                New, Active, Closed
        description : str, optional
            A description of the incident, by default None
        first_activity_time : str, optional
            The start time of the incident activity, by default None
        last_activity_time : str, optional
            The end time of the incident activity, by default None
        labels : List, optional
            Any labels to apply to the incident, by default None
        bookmarks : List, optional
            A list of bookmark GUIDS you want to associate with the incident
        res_id : str, optional
            Resource ID of the workspace, if not provided details from config file will be used.
        sub_id : str, optional
            Sub ID of the workspace, to be used if not providing Resource ID.
        res_grp : str, optional
            Resource Group name of the workspace, to be used if not providing Resource ID.
        ws_name : str, optional
            Workspace name of the workspace, to be used if not providing Resource ID.

        Raises
        ------
        CloudError
            If the API returns an error
        """
        res_id = res_id or self.res_id or self._get_default_workspace()
        if not res_id:
            res_id = self._build_res_id(sub_id, res_grp, ws_name)
        res_id = _validate_res_id(res_id)
        incident_id = uuid4()
        url = self._build_paths(res_id, self.base_url)
        incidents_url = url + _PATH_MAPPING["incidents"]
        incident_url = incidents_url + f"/{incident_id}"
        params = {"api-version": "2020-01-01"}
        data_items = {
            "title": title,
            "severity": severity.capitalize(),
            "status": status.capitalize(),
        }
        if description:
            data_items["description"] = description
        if labels:
            labels = [{"labelName": lab, "labelType": "User"} for lab in labels]
            data_items["labels"] = labels
        # ToDo add some error checking/formatting for the datetimes
        if first_activity_time:
            data_items["firstActivityTimeUtc"] = first_activity_time
        if last_activity_time:
            data_items["lastActivityTimeUtc"] = last_activity_time
        data = _build_data(data_items, props=True)
        response = requests.put(
            incident_url,
            headers=_get_api_headers(self.token),
            params=params,
            data=str(data),
        )
        if response.status_code != 201:
            raise CloudError(response=response)
        if bookmarks:
            for mark in bookmarks:
                relation_id = uuid4()
                bookmark_id = self._get_bookmark_id(mark, res_id)
                mark_res_id = (
                    self._build_paths(res_id, self.base_url)
                    + _PATH_MAPPING["bookmarks"]
                    + f"/{bookmark_id}"
                )
                bookmark_url = incident_url + f"/relations/{relation_id}"
                bkmark_data_items = {"relatedResourceId": mark_res_id}
                data = _build_data(bkmark_data_items, props=True)
                params = {"api-version": "2021-04-01"}
                response = requests.put(
                    bookmark_url,
                    headers=_get_api_headers(self.token),
                    params=params,
                    data=str(data),
                )
        print("Incident created.")

    def _get_incident_id(self, incident: str, res_id: str) -> str:
        """Get an incident ID

        Parameters
        ----------
        incident : str
            An incident identifier
        res_id : str
            The resource ID of the Sentinel Workspace incident is in

        Returns
        -------
        str
            The Incident GUID

        Raises
        ------
        MsticpyUserError
            If incident can't be found or multiple matching incidents found.

        """
        try:
            UUID(incident)
            return incident
        except ValueError:
            incidents = self.list_incidents(res_id)
            filtered_incidents = incidents[
                incidents["properties.title"].str.contains(incident)
            ]
            if len(filtered_incidents) > 1:
                display(filtered_incidents[["name", "properties.title"]])
                raise MsticpyUserError(
                    "More than one incident found, please specify by GUID"
                )
            if (
                not isinstance(filtered_incidents, pd.DataFrame)
                or filtered_incidents.empty
            ):
                raise MsticpyUserError(f"Incident {incident} not found")
            return filtered_incidents["name"].iloc[0]

    def _get_bookmark_id(self, bookmark: str, res_id: str) -> str:
        try:
            UUID(bookmark)
            return bookmark
        except ValueError as bkmark_name:
            bookmarks = self.list_bookmarks(res_id)
            filtered_bookmarks = bookmarks[
                bookmarks["properties.displayName"].str.contains(bookmark)
            ]
            if len(filtered_bookmarks) > 1:
                display(filtered_bookmarks[["name", "properties.displayName"]])
                raise MsticpyUserError(
                    "More than one incident found, please specify by GUID"
                ) from bkmark_name
            if (
                not isinstance(filtered_bookmarks, pd.DataFrame)
                or filtered_bookmarks.empty
            ):
                raise MsticpyUserError(
                    f"Incident {bookmark} not found"
                ) from bkmark_name
            return filtered_bookmarks["name"].iloc[0]

    def add_bookmark_to_incident(
        self,
        incident: str,
        bookmark: str,
        res_id: str = None,
        sub_id: str = None,
        res_grp: str = None,
        ws_name: str = None,
    ):
        """Add a bookmark to an incident.

        Parameters
        ----------
        incident : str
            Either an incident name or an incident GUID
        bookmark_id : str
            Either a bookmakr name or bookmark GUID
        res_id : str, optional
            Resource ID of the workspace, if not provided details from config file will be used.
        sub_id : str, optional
            Sub ID of the workspace, to be used if not providing Resource ID.
        res_grp : str, optional
            Resource Group name of the workspace, to be used if not providing Resource ID.
        ws_name : str, optional
            Workspace name of the workspace, to be used if not providing Resource ID.

        Raises
        ------
        CloudError
            [description]
        """
        res_id = res_id or self.res_id or self._get_default_workspace()
        if not res_id:
            res_id = self._build_res_id(sub_id, res_grp, ws_name)
        res_id = _validate_res_id(res_id)
        incident_id = self._get_incident_id(incident, res_id)
        url = self._build_paths(res_id, self.base_url)
        incidents_url = url + _PATH_MAPPING["incidents"]
        incident_url = incidents_url + f"/{incident_id}"
        bookmark_id = self._get_bookmark_id(bookmark, res_id)
        mark_res_id = (
            self._build_paths(res_id, self.base_url)
            + _PATH_MAPPING["bookmarks"]
            + f"/{bookmark_id}"
        )
        relations_id = uuid4()
        bookmark_url = incident_url + f"/relations/{relations_id}"
        bkmark_data_items = {"relatedResourceId": mark_res_id}
        data = _build_data(bkmark_data_items, props=True)
        params = {"api-version": "2021-04-01"}
        response = requests.put(
            bookmark_url,
            headers=_get_api_headers(self.token),
            params=params,
            data=str(data),
        )
        if response.status_code != 201:
            raise CloudError(response=response)
        print("Bookmark added to incident.")

    def post_comment(
        self,
        incident_id: str,
        comment: str,
        res_id: str = None,
        sub_id: str = None,
        res_grp: str = None,
        ws_name: str = None,
    ):
        """
        Write a comment for an incident.

        Parameters
        ----------
        incident_id : str
            Incident ID GUID.
        comment : str
            Comment message to post.
        res_id : str, optional
            Resource ID of the workspace, if not provided details from config file will be used.
        sub_id : str, optional
            Sub ID of the workspace, to be used if not providing Resource ID.
        res_grp : str, optional
            Resource Group name of the workspace, to be used if not providing Resource ID.
        ws_name : str, optional
            Workspace name of the workspace, to be used if not providing Resource ID.

        Raises
        ------
        CloudError
            If message could not be posted.

        """
        res_id = res_id or self.res_id or self._get_default_workspace()
        if not res_id:
            res_id = self._build_res_id(sub_id, res_grp, ws_name)
        res_id = _validate_res_id(res_id)

        url = self._build_paths(res_id, self.base_url)
        incident_url = url + _PATH_MAPPING["incidents"]
        comment_url = incident_url + f"/{incident_id}/comments/{uuid4()}"
        params = {"api-version": "2020-01-01"}
        data = _build_data({"message": comment})
        response = requests.put(
            comment_url,
            headers=_get_api_headers(self.token),
            params=params,
            data=str(data),
        )
        if response.status_code == 201:
            print("Comment posted.")
        else:
            raise CloudError(response=response)

    def list_data_connectors(
        self,
        res_id: str = None,
        sub_id: str = None,
        res_grp: str = None,
        ws_name: str = None,
    ) -> pd.DataFrame:
        """List deployed data connectors.

        Parameters
        ----------
        res_id : str, optional
            Resource ID of the workspace, if not provided details from config file will be used.
        sub_id : str, optional
            Sub ID of the workspace, to be used if not providing Resource ID.
        res_grp : str, optional
            Resource Group name of the workspace, to be used if not providing Resource ID.
        ws_name : str, optional
            Workspace name of the workspace, to be used if not providing Resource ID.

        Returns
        -------
        pd.DataFrame
            A DataFrame containing the deployed data connectors

        Raises
        ------
        CloudError
            If a valid result is not returned.

        """
        return self._list_items(
            item_tpye="data_connectors",
            res_id=res_id,
            sub_id=sub_id,
            res_grp=res_grp,
            ws_name=ws_name,
        )

    def _get_template_id(
        self,
        template: str,
        res_id: str,
    ) -> str:
        """Get an analytic template ID

        Parameters
        ----------
        template : str
            Template ID or Name
        res_id : str
            Sentinel workspace to get template from

        Returns
        -------
        str
            Template ID

        Raises
        ------
        MsticpyUserError
            If template not found or multiple templates found.
        """
        try:
            UUID(template)
            return template
        except ValueError as template_name:
            templates = self.list_analytic_templates(res_id)
            template = templates[
                templates["properties.displayName"].str.contains(template)
            ]
            if len(template) > 1:
                display(template[["name", "properties.displayName"]])
                raise MsticpyUserError(
                    "More than one template found, please specify by GUID"
                ) from template_name
            if not isinstance(template, pd.DataFrame) or template.empty:
                raise MsticpyUserError(
                    f"Template {template} not found"
                ) from template_name
            return template["name"].iloc[0]

    def create_analytic_rule(
        self,
        template: str = None,
        name: str = None,
        enabled: bool = True,
        query: str = None,
        queryFrequency: str = "PT5H",
        queryPeriod: str = "PT5H",
        severity: str = "Medium",
        suppressionDuration: str = "PT1H",
        suppressionEnabled: bool = False,
        triggerOperator: str = "GreaterThan",
        triggerThreshold: int = 0,
        description: str = None,
        tactics: list = [],
        res_id: str = None,
        sub_id: str = None,
        res_grp: str = None,
        ws_name: str = None,
    ):
        """Create a Sentinel Analytics Rule

        Parameters
        ----------
        template : str, optional
            The GUID or name of a templated to create the analytic from, by default None
        name : str, optional
            The name to give the analytic, by default None
        enabled : bool, optional
            Whether you want the analytic to be enabled once deployed, by default True
        query : str, optional
            The query string to use in the anlaytic, by default None
        queryFrequency : str, optional
            How often the query should run in ISO8601 format, by default "PT5H"
        queryPeriod : str, optional
            How far back the query should look in ISO8601 format, by default "PT5H"
        severity : str, optional
            The severity to raise incidents as, by default "Medium"
            Options are; Informational, Low, Medium, or High
        suppressionDuration : str, optional
            How long to suppress duplicate alerts in ISO8601 format, by default "PT1H"
        suppressionEnabled : bool, optional
            Whether you want to suppress duplicates, by default False
        triggerOperator : str, optional
            The operator for the trigger, by default "GreaterThan"
        triggerThreshold : int, optional
            The threshold of events required to create the incident, by default 0
        description : str, optional
            A description of the analytic, by default None
        tactics : list, optional
            A list of MITRE ATT&CK tactics related to the analytic, by default []
        res_id : str, optional
            Resource ID of the workspace, if not provided details from config file will be used.
        sub_id : str, optional
            Sub ID of the workspace, to be used if not providing Resource ID.
        res_grp : str, optional
            Resource Group name of the workspace, to be used if not providing Resource ID.
        ws_name : str, optional
            Workspace name of the workspace, to be used if not providing Resource ID.

        Raises
        ------
        MsticpyUserError
            If template provided isn't found.
        CloudError
            If the API returns an error.
        """
        res_id = res_id or self.res_id or self._get_default_workspace()
        if not res_id:
            res_id = self._build_res_id(sub_id, res_grp, ws_name)
        res_id = _validate_res_id(res_id)
        if template:
            template_id = self._get_template_id(template, res_id)
            templates = self.list_analytic_templates(res_id)
            template = templates[templates["name"] == template_id].iloc[0]
            name = template["properties.displayName"]
            query = template["properties.query"]
            queryFrequency = template["properties.queryFrequency"]
            queryPeriod = template["properties.queryPeriod"]
            severity = template["properties.severity"]
            triggerOperator = template["properties.triggerOperator"]
            triggerThreshold = template["properties.triggerThreshold"]
            description = template["properties.description"]
            tactics = (
                template["properties.tactics"]
                if not pd.isna(template["properties.tactics"])
                else []
            )

        if not name:
            raise MsticpyUserError(
                "Please specify either a template ID or analytic details."
            )

        rule_id = uuid4()
        url = self._build_paths(res_id, self.base_url)
        analytic_url = url + _PATH_MAPPING["alert_rules"] + f"/{rule_id}"
        data_items = {
            "displayName": name,
            "query": query,
            "queryFrequency": queryFrequency,
            "queryPeriod": queryPeriod,
            "severity": severity,
            "suppressionDuration": suppressionDuration,
            "suppressionEnabled": str(suppressionEnabled).lower(),
            "triggerOperator": triggerOperator,
            "triggerThreshold": triggerThreshold,
            "description": description,
            "tactics": tactics,
            "enabled": str(enabled).lower(),
        }
        data = _build_data(data_items, props=True)
        data["kind"] = "Scheduled"
        params = {"api-version": "2020-01-01"}
        response = requests.put(
            analytic_url,
            headers=_get_api_headers(self.token),
            params=params,
            data=str(data),
        )
        if response.status_code != 201:
            raise CloudError(response=response)
        print("Analytic Created.")

    def _get_analytic_id(self, analytic: str, res_id: str) -> str:
        """Get the GUID of an analytic rule

        Parameters
        ----------
        analytic : str
            The GUID or name of the analytic
        res_id : str
            Sentinel workspace to delete analytic from

        Returns
        -------
        str
            The analytic GUID

        Raises
        ------
        MsticpyUserError
            If analytic not found or multiple matching analytics found
        """
        try:
            UUID(analytic)
            return analytic
        except ValueError as analytic_name:
            analytics = self.list_analytic_rules(res_id)
            analytic = analytics[
                analytics["properties.displayName"].str.contains(analytic)
            ]
            if len(analytic) > 1:
                display(analytic[["name", "properties.displayName"]])
                raise MsticpyUserError(
                    "More than one analytic found, please specify by GUID"
                ) from analytic_name
            if not isinstance(analytic, pd.DataFrame) or analytic.empty:
                raise MsticpyUserError(
                    f"Analytic {analytic} not found"
                ) from analytic_name
            return analytic["name"].iloc[0]

    def delete_analytic_rule(
        self,
        analytic_rule: str,
        res_id: str = None,
        sub_id: str = None,
        res_grp: str = None,
        ws_name: str = None,
    ):
        """Delete a deployed Analytic rule from a Sentinel workspace

        Parameters
        ----------
        analytic_rule : str
            The GUID or name of the analytic.
        res_id : str, optional
            Resource ID of the workspace, if not provided details from config file will be used.
        sub_id : str, optional
            Sub ID of the workspace, to be used if not providing Resource ID.
        res_grp : str, optional
            Resource Group name of the workspace, to be used if not providing Resource ID.
        ws_name : str, optional
            Workspace name of the workspace, to be used if not providing Resource ID.

        Raises
        ------
        CloudError
            If the API returns an error.
        """
        res_id = res_id or self.res_id or self._get_default_workspace()
        if not res_id:
            res_id = self._build_res_id(sub_id, res_grp, ws_name)
        res_id = _validate_res_id(res_id)
        analytic_id = self._get_analytic_id(analytic_rule, res_id)
        url = self._build_paths(res_id, self.base_url)
        analytic_url = url + _PATH_MAPPING["alert_rules"] + f"/{analytic_id}"
        params = {"api-version": "2020-01-01"}
        response = requests.delete(
            analytic_url,
            headers=_get_api_headers(self.token),
            params=params,
        )
        if response.status_code != 200:
            raise CloudError(response=response)
        print("Analytic Deleted.")

    def list_analytic_templates(
        self,
        res_id: str = None,
        sub_id: str = None,
        res_grp: str = None,
        ws_name: str = None,
    ) -> pd.DataFrame:
        """List Analytic Templates

        Parameters
        ----------
        res_id : str, optional
            Resource ID of the workspace, if not provided details from config file will be used.
        sub_id : str, optional
            Sub ID of the workspace, to be used if not providing Resource ID.
        res_grp : str, optional
            Resource Group name of the workspace, to be used if not providing Resource ID.
        ws_name : str, optional
            Workspace name of the workspace, to be used if not providing Resource ID.

        Returns
        -------
        pd.DataFrame
            A DataFrame containing the analytics templates

        Raises
        ------
        CloudError
            If a valid result is not returned.

        """
        return self._list_items(
            item_tpye="alert_template",
            res_id=res_id,
            sub_id=sub_id,
            res_grp=res_grp,
            ws_name=ws_name,
        )

    def list_watchlists(
        self,
        res_id: str = None,
        sub_id: str = None,
        res_grp: str = None,
        ws_name: str = None,
    ) -> pd.DataFrame:
        """List Deployed Watchlists

        Parameters
        ----------
        res_id : str, optional
            Resource ID of the workspace, if not provided details from config file will be used.
        sub_id : str, optional
            Sub ID of the workspace, to be used if not providing Resource ID.
        res_grp : str, optional
            Resource Group name of the workspace, to be used if not providing Resource ID.
        ws_name : str, optional
            Workspace name of the workspace, to be used if not providing Resource ID.

        Returns
        -------
        pd.DataFrame
            A DataFrame containing the watchlists

        Raises
        ------
        CloudError
            If a valid result is not returned.

        """
        return self._list_items(
            item_tpye="watchlists",
            api_version="2021-04-01",
            res_id=res_id,
            sub_id=sub_id,
            res_grp=res_grp,
            ws_name=ws_name,
        )

    def create_watchlist(
        self,
        watchlist_name: str,
        description: str,
        search_key: str,
        provider: str = "MSTICPy",
        source: str = "Notebook",
        data: pd.DataFrame = None,
        res_id: str = None,
        sub_id: str = None,
        res_grp: str = None,
        ws_name: str = None,
    ):
        """Create a new watchlist

        Parameters
        ----------
        watchlist_name : str
            The name of the watchlist you want to create, this can't be the name of an existing watchlist.
        description : str
            A description of the watchlist to be created.
        search_key : str
            The search key is used to optimize query performance when using watchlists for joins with other data.
            This should be the key column that will be used in the watchlist when joining to other data tables.
        provider : str, optional
            This is the label attached to the watchlist showing who created it, by default "MSTICPy"
        source : str, optional
            The source of the data to be put in the watchlist, by default "Notebook"
        data: pd.DataFrame, optional
            The data you want to upload to the watchlist
        res_id : str, optional
            Resource ID of the workspace, if not provided details from config file will be used.
        sub_id : str, optional
            Sub ID of the workspace, to be used if not providing Resource ID.
        res_grp : str, optional
            Resource Group name of the workspace, to be used if not providing Resource ID.
        ws_name : str, optional
            Workspace name of the workspace, to be used if not providing Resource ID.

        Raises
        ------
        MsticpyUserError
            Raised if the watchlist name already exists.
        CloudError
            If there is an issue creating the watchlist.
        """
        res_id = res_id or self.res_id or self._get_default_workspace()
        if not res_id:
            res_id = self._build_res_id(sub_id, res_grp, ws_name)
        res_id = _validate_res_id(res_id)

        if not self._check_watchlist_exists(watchlist_name, res_id):
            raise MsticpyUserError(f"Watchlist {watchlist_name} does not exist.")

        url = self._build_paths(res_id, self.base_url)
        watchlist_url = url + _PATH_MAPPING["watchlists"] + f"/{watchlist_name}"
        params = {"api-version": "2021-04-01"}
        data_items = {
            "displayName": watchlist_name,
            "source": source,
            "provider": provider,
            "description": description,
            "itemsSearchKey": search_key,
            "contentType": "text/csv",
        }
        if isinstance(data, pd.DataFrame) and not data.empty:
            data = data.to_csv(index=False)
            data_items["rawContent"] = data
        data = _build_data(data_items, props=True)
        response = requests.put(
            watchlist_url,
            headers=_get_api_headers(self.token),
            params=params,
            data=str(data),
        )
        if response.status_code == 200:
            print("Watchlist created.")
        else:
            raise CloudError(response=response)

    def list_watchlist_items(
        self,
        watchlist_name: str,
        res_id: str = None,
        sub_id: str = None,
        res_grp: str = None,
        ws_name: str = None,
    ) -> pd.DataFrame:
        """List items in a watchlist

        Parameters
        ----------
        watchlist_name : str
            The name of the watchlist to get items from
        res_id : str, optional
            Resource ID of the workspace, if not provided details from config file will be used.
        sub_id : str, optional
            Sub ID of the workspace, to be used if not providing Resource ID.
        res_grp : str, optional
            Resource Group name of the workspace, to be used if not providing Resource ID.
        ws_name : str, optional
            Workspace name of the workspace, to be used if not providing Resource ID.

        Returns
        -------
        pd.DataFrame
            A DataFrame containing the watchlists

        Raises
        ------
        CloudError
            If a valid result is not returned.

        """
        watchlist_name_str = f"/{watchlist_name}/watchlistItems"
        return self._list_items(
            item_tpye="watchlists",
            api_version="2021-04-01",
            res_id=res_id,
            sub_id=sub_id,
            res_grp=res_grp,
            ws_name=ws_name,
            appendix=watchlist_name_str,
        )

    def add_watchlist_item(
        self,
        watchlist_name: str,
        item: Union[Dict, pd.Series, pd.DataFrame],
        overwrite: bool = False,
        res_id: str = None,
        sub_id: str = None,
        res_grp: str = None,
        ws_name: str = None,
    ):
        """Add or update an item in a Watchlist

        Parameters
        ----------
        watchlist_name : str
            The name of the watchlist to add items to
        item : Union[Dict, pd.Series, pd.DataFrame]
            The item to add, this can be a dictionary of valies, a Pandas Series, or a Pandas DataFrame
        overwrite : bool, optional
            Wether you want to overwrite an item if it already exists in the watchlist, by default False
        res_id : str, optional
            Resource ID of the workspace, if not provided details from config file will be used.
        sub_id : str, optional
            Sub ID of the workspace, to be used if not providing Resource ID.
        res_grp : str, optional
            Resource Group name of the workspace, to be used if not providing Resource ID.
        ws_name : str, optional
            Workspace name of the workspace, to be used if not providing Resource ID.

        Raises
        ------
        MsticpyUserError
            If the specified Watchlist does not exist.
        MsticpyUserError
            If the item already exists in the Watchlist and overwrite is set to False
        CloudError
            If the API returns an error.
        """
        # Generate or use resource ID
        res_id = res_id or self.res_id or self._get_default_workspace()
        if not res_id:
            res_id = self._build_res_id(sub_id, res_grp, ws_name)
        res_id = _validate_res_id(res_id)
        # Check requested watchlist actually exists
        if not self._check_watchlist_exists(watchlist_name, res_id):
            raise MsticpyUserError(f"Watchlist {watchlist_name} does not exist.")

        new_items = []
        # Convert items to add to dictionary format
        if isinstance(item, pd.Series):
            new_items = [dict(item)]
        elif isinstance(item, Dict):
            new_items = [item]
        elif isinstance(item, pd.DataFrame):
            for _, line_item in item.iterrows():
                new_items.append(dict(line_item))

        current_items = self.list_watchlist_items(
            res_id=res_id, watchlist_name=watchlist_name
        )
        current_items_values = current_items.filter(
            regex="^properties.itemsKeyValue.", axis=1
        )
        current_items_values.columns = current_items_values.columns.str.replace(
            "properties.itemsKeyValue.", "", regex=False
        )

        for item in new_items:
            # See if item already exists, if it does get the item ID
            current_df, item_series = current_items_values.align(
                pd.Series(item), axis=1, copy=False
            )
            if (current_df == item_series).all(axis=1).any() and overwrite:
                id = current_items[current_items.isin(list(item.values())).any(axis=1)][
                    "properties.watchlistItemId"
                ].iloc[0]
            # If not in watchlist already generate new ID
            elif not (current_df == item_series).all(axis=1).any():
                id = str(uuid4())
            else:
                raise MsticpyUserError(
                    "Item already exists in the watchlist. Set overwrite = True to replace."
                )

            url = self._build_paths(res_id, self.base_url)
            watchlist_url = (
                url
                + _PATH_MAPPING["watchlists"]
                + f"/{watchlist_name}/watchlistItems/{id}"
            )
            params = {"api-version": "2021-04-01"}
            data = {"properties": {"itemsKeyValue": item}}
            response = requests.put(
                watchlist_url,
                headers=_get_api_headers(self.token),
                params=params,
                data=str(data),
            )
            if response.status_code == 200:
                continue
            else:
                raise CloudError(response=response)

        print(f"Items added to {watchlist_name}")

    def delete_watchlist(
        self,
        watchlist_name: str,
        res_id: str = None,
        sub_id: str = None,
        res_grp: str = None,
        ws_name: str = None,
    ):
        """Delete a selected Watchlist

        Parameters
        ----------
        watchlist_name : str
            The name of the Watchlist to deleted
        res_id : str, optional
            Resource ID of the workspace, if not provided details from config file will be used.
        sub_id : str, optional
            Sub ID of the workspace, to be used if not providing Resource ID.
        res_grp : str, optional
            Resource Group name of the workspace, to be used if not providing Resource ID.
        ws_name : str, optional
            Workspace name of the workspace, to be used if not providing Resource ID.

        Raises
        ------
        MsticpyUserError
            If Watchlist does not exist.
        CloudError
            If the API returns an error.
        """
        res_id = res_id or self.res_id or self._get_default_workspace()
        if not res_id:
            res_id = self._build_res_id(sub_id, res_grp, ws_name)
        res_id = _validate_res_id(res_id)
        # Check requested watchlist actually exists
        if not self._check_watchlist_exists(watchlist_name, res_id):
            raise MsticpyUserError(f"Watchlist {watchlist_name} does not exist.")

        url = self._build_paths(res_id, self.base_url)
        watchlist_url = url + _PATH_MAPPING["watchlists"] + f"/{watchlist_name}"
        params = {"api-version": "2021-04-01"}
        response = requests.delete(
            watchlist_url,
            headers=_get_api_headers(self.token),
            params=params,
        )
        if response.status_code == 200:
            print(f"Watchlist {watchlist_name} deleted")
        else:
            raise CloudError(response=response)

    def _check_watchlist_exists(
        self,
        watchlist_name: str,
        res_id: str = None,
    ):
        """Checks whether a Watchlist exists or not.

        Parameters
        ----------
        watchlist_name : str
            The Watchlist to check for.
        res_id : str, optional
            The Resource ID of the Sentinel workspace to check in, by default None

        Returns
        -------
        bool
            Whether the Watchlist exists or not.
        """
        # Check requested watchlist actually exists
        existing_watchlists = self.list_watchlists(res_id)["name"].values
        return watchlist_name in existing_watchlists

    def _list_items(
        self,
        item_tpye: str,
        api_version: str = "2020-01-01",
        res_id: str = None,
        sub_id: str = None,
        res_grp: str = None,
        ws_name: str = None,
        appendix: str = None,
    ) -> pd.DataFrame:
        """Returns lists of core resources from APIs

        Parameters
        ----------
        item_tpye : str
            The type of resource you want to list.
        api_version: str, optional
            The API version to use, by default '2020-01-01'
        res_id : str, optional
            Resource ID of the workspace, if not provided details from config file will be used.
        sub_id : str, optional
            Sub ID of the workspace, to be used if not providing Resource ID.
        res_grp : str, optional
            Resource Group name of the workspace, to be used if not providing Resource ID.
        ws_name : str, optional
            Workspace name of the workspace, to be used if not providing Resource ID.
        Returns
        -------
        pd.DataFrame
            A DataFrame containing the requested items.

        Raises
        ------
        CloudError
            If a valid result is not returned.

        """
        res_id = res_id or self.res_id or self._get_default_workspace()
        if not res_id:
            res_id = self._build_res_id(sub_id, res_grp, ws_name)
        res_id = _validate_res_id(res_id)

        url = self._build_paths(res_id, self.base_url)
        item_url = url + _PATH_MAPPING[item_tpye]
        if appendix:
            item_url = item_url + appendix
        params = {"api-version": api_version}
        response = requests.get(
            item_url, headers=_get_api_headers(self.token), params=params
        )
        if response.status_code == 200:
            results_df = _azs_api_result_to_df(response)
        else:
            raise CloudError(response=response)

        return results_df

    # Get > List Aliases
    get_alert_rules = list_alert_rules
    list_analytic_rules = list_alert_rules
    get_analytic_rules = list_alert_rules
    get_sentinel_workspaces = list_sentinel_workspaces
    get_hunting_queries = list_hunting_queries
    get_bookmarks = list_bookmarks
    get_incidents = list_incidents

    def _check_config(self, items: List) -> Dict:
        """
        Get parameters from default config files.

        Parameters
        ----------
        items : List
            The items to get from the config.

        Returns
        -------
        Dict
            The config items.

        """
        config_items = {}
        if not self.config:
            self.config = WorkspaceConfig()  # type: ignore
        for item in items:
            if item in self.config:  # type: ignore
                config_items[item] = self.config[item]
            else:
                raise MsticpyAzureConfigError(f"No {item} avaliable in config.")

        return config_items

    def _build_res_id(
        self, sub_id: str = None, res_grp: str = None, ws_name: str = None
    ) -> str:
        """
        Build a resource ID.

        Parameters
        ----------
        sub_id : str, optional
            Subscription ID to use, by default None
        res_grp : str, optional
            Resource Group name to use, by default None
        ws_name : str, optional
            Workspace name to user, by default None

        Returns
        -------
        str
            The formatted resource ID.

        """
        if not sub_id or not res_grp or not ws_name:
            config = self._check_config(
                ["subscription_id", "resource_group", "workspace_name"]
            )
            sub_id = config["subscription_id"]
            res_grp = config["resource_group"]
            ws_name = config["workspace_name"]
        return "".join(
            [
                f"/subscriptions/{sub_id}/resourcegroups/{res_grp}",
                f"/providers/Microsoft.OperationalInsights/workspaces/{ws_name}",
            ]
        )

    def _build_paths(self, res_id: str, base_url: str = None) -> str:
        """
        Build an API URL from an Azure resource ID.

        Parameters
        ----------
        res_id : str
            An Azure resource ID.
        base_url : str, optional
            The base URL of the Azure cloud to connect to.
            Defaults to resource manager for configured cloud.
            If no cloud configuration, defaults to resource manager
            endpoint for public cloud.

        Returns
        -------
        str
            A URI to that resource.

        """
        if not base_url:
            base_url = AzureCloudConfig(self.cloud).endpoints.resource_manager
        res_info = {
            "subscription_id": res_id.split("/")[2],
            "resource_group": res_id.split("/")[4],
            "workspace_name": res_id.split("/")[-1],
        }

        return "".join(
            [
                f"{base_url}/subscriptions/{res_info['subscription_id']}",
                f"/resourceGroups/{res_info['resource_group']}",
                "/providers/Microsoft.OperationalInsights/workspaces"
                f"/{res_info['workspace_name']}",
            ]
        )


def _get_token(credential: AzCredentials) -> str:
    """
    Extract token from a azure.identity object.

    Parameters
    ----------
    credential : AzCredentials
        Azure OAuth credentials.

    Returns
    -------
    str
        A token to be used in API calls.

    """
    token = credential.modern.get_token(AzureCloudConfig().token_uri)
    return token.token


def _get_api_headers(token: str) -> Dict:
    """
    Return authorization header with current token.

    Parameters
    ----------
    token : str
        Azure auth token.

    Returns
    -------
    Dict
        A dictionary of headers to be used in API calls.

    """
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _azs_api_result_to_df(response: requests.Response) -> pd.DataFrame:
    """
    Convert API response to a Pandas dataframe.

    Parameters
    ----------
    response : requests.Response
        A response object from an Azure REST API call.

    Returns
    -------
    pd.DataFrame
        The API response as a Pandas dataframe.

    Raises
    ------
    ValueError
        If the response is not valid JSON.

    """
    j_resp = response.json()
    if response.status_code != 200 or not j_resp:
        raise ValueError("No valid JSON result in response")
    if "value" in j_resp:
        j_resp = j_resp["value"]
    return pd.json_normalize(j_resp)


def _build_data(items: dict, props: bool = False, **kwargs) -> dict:
    """
    Build request data body from items.

    Parameters
    ----------
    items : dict
        A set pf items to be formated in the request body.
    props: bool, optional
        Whether all items are to be built as properities. Default is false.

    Returns
    -------
    dict
        The request body formatted for the API.

    """
    data_body = {"properties": {}}  # type: Dict[str, Dict[str, str]]
    for key, _ in items.items():
        if key in ["severity", "status", "title", "message"] or props:
            data_body["properties"].update({key: items[key]})  # type:ignore
        else:
            data_body[key] = items[key]
    if "etag" in kwargs:
        data_body["etag"] = kwargs.get("etag")
    return data_body


def _validate_res_id(res_id):
    """Validate a Resource ID String and fix if needed."""
    valid = _validator(res_id)
    if not valid:
        res_id = _fix_res_id(res_id)
        valid = _validator(res_id)
    if not valid:
        raise MsticpyAzureConfigError(
            "The Sentinel Workspace Resource ID provided is not valid."
        )
    else:
        return res_id


def _validator(res_id):
    """Check Resource ID string matches pattern expected."""
    counts = Counter(res_id)
    return bool(
        res_id.startswith("/") and counts["/"] == 8 and not res_id.endswith("/")
    )


def _fix_res_id(res_id):
    """Try to fix common issues with Resource ID string."""
    if res_id.startswith("https:"):
        res_id = "/".join(res_id.split("/")[5:])
    if not res_id.startswith("/"):
        res_id = "/" + res_id
    if res_id.endswith("/"):
        res_id = res_id[:-1]
    counts = Counter(res_id)
    if counts["/"] > 8:
        res_id = "/".join(res_id.split("/")[:9])
    return res_id


MicrosoftSentinel = AzureSentinel
