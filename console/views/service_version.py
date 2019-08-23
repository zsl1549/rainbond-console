# -*- coding: utf8 -*-
"""
  Created on 2018/6/21.
"""
import logging
import operator

from dateutil import parser
from django.core.paginator import Paginator
from django.views.decorators.cache import never_cache
from rest_framework.response import Response

from console.repositories.event_repo import event_repo
from console.utils.timeutil import current_time_str
from console.views.app_config.base import AppBaseView
from www.apiclient.regionapi import RegionInvokeApi
from www.decorator import perm_required
from www.utils.return_message import error_message
from www.utils.return_message import general_message

logger = logging.getLogger("default")

region_api = RegionInvokeApi()

BUILD_KIND_MAP = {
    "build_from_source_code": "源码构建",
    "build_from_image": "镜像构建",
    "build_from_market_image": "云市镜像构建",
    "build_from_market_slug": "云市slug包构建"
}


class AppVersionsView(AppBaseView):
    @never_cache
    @perm_required('view_service')
    def get(self, request, *args, **kwargs):
        """
        获取服务的构建版本
        ---
        parameters:
            - name: tenantName
              description: 租户名
              required: true
              type: string
              paramType: path
            - name: serviceAlias
              description: 服务别名
              required: true
              type: string
              paramType: path
        """
        try:
            page = request.GET.get("page_num", 1)
            page_size = request.GET.get("page_size", 10)
            body = region_api.get_service_build_versions(self.response_region, self.tenant.tenant_name,
                                                         self.service.service_alias)
            build_version_sort = body["bean"]["list"]
            run_version = body["bean"]["deploy_version"]
            total_num_list = list()
            for build_version_info in build_version_sort:
                if build_version_info["final_status"] in ("success", "failure"):
                    total_num_list.append(build_version_info)
            total_num = len(total_num_list)
            success_num = 0
            failure_num = 0
            for build_info in build_version_sort:
                if build_info["final_status"]:
                    if build_info["final_status"] == "success":
                        success_num += 1
                    else:
                        failure_num += 1
            build_version_sort.sort(key=operator.itemgetter('build_version'), reverse=True)
            paginator = Paginator(build_version_sort, page_size)
            build_version_list = paginator.page(int(page)).object_list

            events = event_repo.get_events_before_specify_time(
                self.tenant.tenant_id,
                self.service.service_id,
                current_time_str(fmt="%Y-%m-%d %H:%M:%S")).filter(type="deploy")
            version_user_map = {event.event_id: event.user_name for event in events}

            versions_info = build_version_list
            version_list = []

            def cul_delta(startstr, endstr):
                start = parser.parse(startstr)
                end = parser.parse(endstr)
                delta = end - start
                return delta.seconds / 3600, (delta.seconds / 60) % 60, delta.seconds % 60

            for info in versions_info:
                version = {
                    "build_version": info["build_version"],
                    "kind": BUILD_KIND_MAP.get(info["kind"]),
                    "service_type": info["delivered_type"],
                    "repo_url": info["repo_url"],
                    "create_time": info["create_time"],
                    "status": info["final_status"],
                    "build_user": version_user_map.get(info["event_id"], "未知"),
                    # source code
                    "code_commit_msg": info["code_commit_msg"],
                    "code_version": info["code_version"],
                    "code_branch": info.get("code_branch", "未知"),
                    "code_commit_author": info["code_commit_author"],
                    # image
                    "image_repo": info["image_repo"],
                    "image_domain": info.get("image_domain", "未知"),
                    "image_tag": info["image_tag"],
                }

                if info["finish_time"] != "0001-01-01T00:00:00Z":
                    version["finish_time"] = info["finish_time"]
                    version["dur_hours"], version["dur_minutes"], version["dur_seconds"] = cul_delta(
                        info["finish_time"], info["create_time"])
                else:
                    version["finish_time"], version["dur_hours"], version["dur_minutes"],
                    version["dur_seconds"] = "", 0, 0, 0

                version_list.append(version)
            res_versions = sorted(version_list, key=lambda version: version["build_version"], reverse=True)
            for res_version in res_versions:
                # get deploy version from region
                if res_version["status"] == "failure":
                    upgrade_or_rollback = 2
                elif int(res_version["build_version"]) > int(run_version):
                    upgrade_or_rollback = 1
                elif int(res_version["build_version"]) == int(run_version):
                    upgrade_or_rollback = 0
                else:
                    upgrade_or_rollback = -1
                res_version.update({"upgrade_or_rollback": upgrade_or_rollback})
            is_upgrade = False
            if res_versions:
                latest_version = res_versions[0]["build_version"]
                if int(latest_version) > int(run_version):
                    is_upgrade = True
            bean = {
                "is_upgrade": is_upgrade,
                "current_version": run_version,
                "success_num": str(success_num),
                "failure_num": str(failure_num)
            }
            result = general_message(200, "success", "查询成功", bean=bean, list=res_versions, total=str(total_num))
            return Response(result, status=result["code"])
        except Exception as e:
            logger.exception(e)
            result = error_message(e.message)
            return Response(result, status=500)


class AppVersionManageView(AppBaseView):
    @never_cache
    @perm_required('manage_service_config')
    def delete(self, request, *args, **kwargs):
        """
        删除应用的某次构建版本
        ---
        parameters:
            - name: tenantName
              description: 租户名
              required: true
              type: string
              paramType: path
            - name: serviceAlias
              description: 服务别名
              required: true
              type: string
              paramType: path
            - name: version_id
              description: 版本ID
              required: true
              type: string
              paramType: path

        """
        version_id = kwargs.get("version_id", None)
        try:
            if not version_id:
                return Response(general_message(400, "attr_name not specify", u"请指定需要删除的具体版本"))
            region_api.delete_service_build_version(
                self.response_region, self.tenant.tenant_name, self.service.service_alias, version_id)
            # event_repo.delete_event_by_build_version(self.service.service_id, version_id)
            result = general_message(200, "success", u"删除成功")
        except Exception as e:
            logger.exception(e)
            result = error_message(e.message)
        return Response(result, status=result["code"])

    @never_cache
    @perm_required('view_service')
    def get(self, request, *args, **kwargs):
        """
        获取应用的某个具体版本
        ---
        parameters:
            - name: tenantName
              description: 租户名
              required: true
              type: string
              paramType: path
            - name: serviceAlias
              description: 服务别名
              required: true
              type: string
              paramType: path
            - name: version_id
              description: 版本id
              required: true
              type: string
              paramType: path

        """
        version_id = kwargs.get("version_id", None)
        try:
            if not version_id:
                return Response(general_message(400, "attr_name not specify", u"请指定需要查询的具体版本"))

            res, body = region_api.get_service_build_version_by_id(self.response_region, self.tenant.tenant_name,
                                                                   self.service.service_alias, version_id)
            data = body['bean']

            result = general_message(200, "success", u"查询成功", bean={"is_exist": data["status"]})
        except Exception as e:
            logger.exception(e)
            result = error_message(e.message)
        return Response(result, status=result["code"])
