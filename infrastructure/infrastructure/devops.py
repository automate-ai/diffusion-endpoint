from constructs import Construct
from aws_cdk import Stack, pipelines, Stage, CfnOutput
from infrastructure.endpoint import EndpointStack
from aws_cdk import aws_codebuild as codebuild
from aws_cdk import aws_ecr as ecr
from aws_cdk.aws_ecr_assets import DockerImageAsset

OWNER_REPO = "Duncan-Haywood/diffusion-endpoint"
BRANCH = "main"


class PipelineStack(Stack):
    def __init__(
        self, scope: Construct, construct_id: str, branch=BRANCH, **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        source = pipelines.CodePipelineSource.git_hub(OWNER_REPO, branch)
        self.pipeline = pipelines.CodePipeline(
            self,
            "Pipeline",
            synth=pipelines.CodeBuildStep(
                "Synth",
                input=source,
                commands=[
                    "cd infrastructure",
                    "pip install poetry",
                    "poetry install",
                    "npm install -g aws-cdk",
                    "poetry run cdk synth --output ../cdk.out",
                ],
            ),
            code_build_defaults=pipelines.CodeBuildOptions(
                build_environment=codebuild.BuildEnvironment(
                    compute_type=codebuild.ComputeType.MEDIUM,
                )
            ),
            synth_code_build_defaults=pipelines.CodeBuildOptions(
                build_environment=codebuild.BuildEnvironment(
                    compute_type=codebuild.ComputeType.MEDIUM,
                )
            ),
            asset_publishing_code_build_defaults=pipelines.CodeBuildOptions(
                build_environment=codebuild.BuildEnvironment(
                    compute_type=codebuild.ComputeType.LARGE,
                ),
                cache=codebuild.Cache.local(codebuild.LocalCacheMode.DOCKER_LAYER),
            ),
            self_mutation_code_build_defaults=pipelines.CodeBuildOptions(
                build_environment=codebuild.BuildEnvironment(
                    compute_type=codebuild.ComputeType.MEDIUM,
                )
            ),
        )
        self.pipeline.add_stage(
            EndpointStage(
                self,
                "TestStage",
                production=False,
            ),
        )
        self.pipeline.add_stage(
            EndpointStage(
                self,
                "ProdStage",
                production=True,
            ),
            pre=[pipelines.ManualApprovalStep("PromoteToProd")],
        )


class EndpointStage(Stage):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        production: bool = False,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        # create general image for tests
        general_image = GeneralImageStack(self, "GeneralImageStack")
        self.general_image_uri = general_image.general_image_uri
        # run unit tests
        pipelines.StackSteps(
            stack=general_image, post=[unit_tests(self.general_image_uri)]
        )
        # create endpoint stack
        self.app = EndpointStack(self, "EndpointStack")
        self.general_image_uri = self.app.general_image_uri

        # add post processing steps with dependency graph
        upload_model_step = upload_model_step(
            self.general_image_uri, self.app.model_bucket_name
        )
        upload_endpoint_step = set_endpoint_in_parameter_store(
            self.general_image_uri, production, self.app.endpoint_name
        )
        integration_test_step = integration_tests(self.general_image_uri)
        integration_test_step.add_step_dependency(upload_endpoint_step)
        integration_test_step.add_step_dependency(upload_model_step)

        pipelines.StackSteps(
            stack=self.app,
            post=[upload_model_step, upload_endpoint_step, integration_test_step],
        )


## functions referenced above


class GeneralImageStack(Stack):
    def __init__(
        self,
        scope: Constrt,
        construct_id: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        # create general image assets
        general_image = DockerImageAsset(
            self,
            "GeneralImage",
            directory="../src/endpoint",
            file="Dockerfile",
        )
        self.general_image_uri = general_image.image_uri


def unit_tests(image_uri):
    return pipelines.CodeBuildStep(
        "UnitTest",
        commands=[
            "pytest --docker-local --upload-model -n $(nproc)",
        ],
        build_environment=codebuild.BuildEnvironment(
            privileged=True,
            compute_type=codebuild.ComputeType.LARGE,
            build_image=image_uri,
        ),
    )


def integration_tests(image_uri):
    return pipelines.CodeBuildStep(
        "UnitTest",
        commands=[
            "pytest --local-integration --integration -n $(nproc)",
        ],
        build_environment=codebuild.BuildEnvironment(
            privileged=True,
            compute_type=codebuild.ComputeType.LARGE,
            build_image=image_uri,
        ),
    )


def set_endpoint_in_parameter_store(image_uri, production, endpoint_name):
    return pipelines.CodeBuildStep(
        "SetEndpointNameInParameterStore",
        commands=[
            "python ./endpoint/param_store_endpoint_name.py",
        ],
        build_environment=codebuild.BuildEnvironment(
            compute_type=codebuild.ComputeType.MEDIUM,
            build_image=image_uri,
        ),
        env={
            "production": str(production),
        },
        env_from_cfn_outputs={
            "endpoint_name": endpoint_name,
        },
    )


def upload_model_step(image_uri, model_bucket_name):
    return pipelines.CodeBuildStep(
        "UploadModel",
        commands=[
            "python ./endpoint/upload_model.py",
        ],
        build_environment=codebuild.BuildEnvironment(
            compute_type=codebuild.ComputeType.LARGE,
            build_image=image_uri,
        ),
        env=dict(model_bucket_name=model_bucket_name),
    )


# def upload_image(image_repo_name, repository_uri, file_name, file_path):
#     return pipelines.CodeBuildStep(
#         "Image",
#         commands=[
#             "docker build --tag $IMAGE_REPO_NAME --file $FILENAME $FILE_PATH",
#             "docker tag $IMAGE_REPO_NAME $REPOSITORY_URI",
#             "docker push $REPOSITORY_URI",
#         ],
#         build_environment=codebuild.BuildEnvironment(
#             privileged=True, compute_type=codebuild.ComputeType.LARGE
#         ),
#         env=dict(
#             IMAGE_REPO_NAME=image_repo_name,
#             REPOSITORY_URI=repository_uri,
#             FILENAME=file_name,
#             FILE_PATH=file_path,
#         ),
#         cache=codebuild.Cache.local(codebuild.LocalCacheMode.DOCKER_LAYER),
#     )


# class AssetStack(Stack):
#     def __init__(
#         self,
#         scope: Construct,
#         construct_id: str,
#         file_name="Dockerfile",
#         file_path="src/endpoint",
#         **kwargs,
#     ) -> None:
#         super().__init__(scope, construct_id, **kwargs)
#         self.repo = ecr.Repository(self, "Repository")
#         image_repo_name = self.repo.repository_name
#         self.repository_uri_str = self.repo.repository_uri
#         pipelines.StackSteps(
#             stack=self,
#             post=[
#                 upload_image(
#                     image_repo_name, self.repository_uri_str, file_name, file_path
#                 )
#             ],
#         )
#         self.repository_uri = CfnOutput(self, "RepoUri", value=self.repo.repository_uri)
