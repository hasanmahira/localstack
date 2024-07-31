# LocalStack Resource Provider Scaffolding v2
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional, TypedDict

import localstack.services.cloudformation.provider_utils as util
from localstack.services.cloudformation.resource_provider import (
    OperationStatus,
    ProgressEvent,
    ResourceProvider,
    ResourceRequest,
)
from localstack.utils.functions import call_safe


class IAMRoleProperties(TypedDict):
    AssumeRolePolicyDocument: Optional[dict | str]
    Arn: Optional[str]
    Description: Optional[str]
    ManagedPolicyArns: Optional[list[str]]
    MaxSessionDuration: Optional[int]
    Path: Optional[str]
    PermissionsBoundary: Optional[str]
    Policies: Optional[list[Policy]]
    RoleId: Optional[str]
    RoleName: Optional[str]
    Tags: Optional[list[Tag]]


class Policy(TypedDict):
    PolicyDocument: Optional[str | dict]
    PolicyName: Optional[str]


class Tag(TypedDict):
    Key: Optional[str]
    Value: Optional[str]


REPEATED_INVOCATION = "repeated_invocation"

IAM_POLICY_VERSION = "2012-10-17"


class IAMRoleProvider(ResourceProvider[IAMRoleProperties]):
    TYPE = "AWS::IAM::Role"  # Autogenerated. Don't change
    SCHEMA = util.get_schema_path(Path(__file__))  # Autogenerated. Don't change

    def create(
        self,
        request: ResourceRequest[IAMRoleProperties],
    ) -> ProgressEvent[IAMRoleProperties]:
        """
        Create a new resource.

        Primary identifier fields:
          - /properties/RoleName

        Required properties:
          - AssumeRolePolicyDocument

        Create-only properties:
          - /properties/Path
          - /properties/RoleName

        Read-only properties:
          - /properties/Arn
          - /properties/RoleId

        IAM permissions required:
          - iam:CreateRole
          - iam:PutRolePolicy
          - iam:AttachRolePolicy
          - iam:GetRolePolicy <- not in use right now

        """
        model = request.desired_state
        iam = request.aws_client_factory.iam

        # defaults
        role_name = model.get("RoleName")
        if not role_name:
            role_name = util.generate_default_name(request.stack_name, request.logical_resource_id)
            model["RoleName"] = role_name

        create_role_response = iam.create_role(
            **{
                k: v
                for k, v in model.items()
                if k not in ["ManagedPolicyArns", "Policies", "AssumeRolePolicyDocument"]
            },
            AssumeRolePolicyDocument=json.dumps(model["AssumeRolePolicyDocument"]),
        )

        # attach managed policies
        policy_arns = model.get("ManagedPolicyArns", [])
        for arn in policy_arns:
            iam.attach_role_policy(RoleName=role_name, PolicyArn=arn)

        # add inline policies
        inline_policies = model.get("Policies", [])
        for policy in inline_policies:
            if not isinstance(policy, dict):
                request.logger.info(
                    'Invalid format of policy for IAM role "%s": %s',
                    model.get("RoleName"),
                    policy,
                )
                continue
            pol_name = policy.get("PolicyName")

            # get policy document - make sure we're resolving references in the policy doc
            doc = dict(policy["PolicyDocument"])
            doc = util.remove_none_values(doc)

            doc["Version"] = doc.get("Version") or IAM_POLICY_VERSION
            statements = doc["Statement"]
            statements = statements if isinstance(statements, list) else [statements]
            for statement in statements:
                if isinstance(statement.get("Resource"), list):
                    # filter out empty resource strings
                    statement["Resource"] = [r for r in statement["Resource"] if r]
            doc = json.dumps(doc)
            iam.put_role_policy(
                RoleName=model["RoleName"],
                PolicyName=pol_name,
                PolicyDocument=doc,
            )
        model["Arn"] = create_role_response["Role"]["Arn"]
        model["RoleId"] = create_role_response["Role"]["RoleId"]

        return ProgressEvent(status=OperationStatus.SUCCESS, resource_model=model)

    def read(
        self,
        request: ResourceRequest[IAMRoleProperties],
    ) -> ProgressEvent[IAMRoleProperties]:
        """
        Fetch resource information

        IAM permissions required:
          - iam:GetRole
          - iam:ListAttachedRolePolicies
          - iam:ListRolePolicies
          - iam:GetRolePolicy
        """
        raise NotImplementedError

    def delete(
        self,
        request: ResourceRequest[IAMRoleProperties],
    ) -> ProgressEvent[IAMRoleProperties]:
        """
        Delete a resource

        IAM permissions required:
          - iam:DeleteRole
          - iam:DetachRolePolicy
          - iam:DeleteRolePolicy
          - iam:GetRole
          - iam:ListAttachedRolePolicies
          - iam:ListRolePolicies
        """
        iam_client = request.aws_client_factory.iam
        role_name = request.previous_state["RoleName"]

        # detach managed policies
        for policy in iam_client.list_attached_role_policies(RoleName=role_name).get(
            "AttachedPolicies", []
        ):
            call_safe(
                iam_client.detach_role_policy,
                kwargs={"RoleName": role_name, "PolicyArn": policy["PolicyArn"]},
            )

        # delete inline policies
        for inline_policy_name in iam_client.list_role_policies(RoleName=role_name).get(
            "PolicyNames", []
        ):
            call_safe(
                iam_client.delete_role_policy,
                kwargs={"RoleName": role_name, "PolicyName": inline_policy_name},
            )

        iam_client.delete_role(RoleName=role_name)
        return ProgressEvent(status=OperationStatus.SUCCESS, resource_model={})

    def update(
        self,
        request: ResourceRequest[IAMRoleProperties],
    ) -> ProgressEvent[IAMRoleProperties]:
        """
        Update a resource

        IAM permissions required:
          - iam:UpdateRole
          - iam:UpdateRoleDescription
          - iam:UpdateAssumeRolePolicy
          - iam:DetachRolePolicy
          - iam:AttachRolePolicy
          - iam:DeleteRolePermissionsBoundary
          - iam:PutRolePermissionsBoundary
          - iam:DeleteRolePolicy
          - iam:PutRolePolicy
          - iam:TagRole
          - iam:UntagRole
        """
        props = request.desired_state
        _states = request.previous_state

        # note that we're using permissions that are not technically allowed here due to the currently broken change detection
        props_policy = props.get("AssumeRolePolicyDocument")
        # technically a change to the role name shouldn't even get here since it implies a replacement, not an in-place update
        # for now we just go with it though
        # determine if the previous name was autogenerated or not
        new_role_name = props.get("RoleName")
        name_changed = new_role_name and new_role_name != _states["RoleName"]

        # new_role_name = props.get("RoleName", _states.get("RoleName"))
        policy_changed = props_policy and props_policy != _states.get(
            "AssumeRolePolicyDocument", ""
        )
        managed_policy_arns_changed = props.get("ManagedPolicyArns", []) != _states.get(
            "ManagedPolicyArns", []
        )
        if name_changed or policy_changed or managed_policy_arns_changed:
            # TODO: do a proper update instead of replacement
            self.delete(request)
            return self.create(request)
        return ProgressEvent(status=OperationStatus.SUCCESS, resource_model=request.previous_state)
        # raise Exception("why was a change even detected?")

    def list(
        self,
        request: ResourceRequest[IAMRoleProperties],
    ) -> ProgressEvent[IAMRoleProperties]:
        resources = request.aws_client_factory.iam.list_roles()
        return ProgressEvent(
            status=OperationStatus.SUCCESS,
            resource_models=[
                IAMRoleProperties(RoleName=resource["RoleName"]) for resource in resources["Roles"]
            ],
        )