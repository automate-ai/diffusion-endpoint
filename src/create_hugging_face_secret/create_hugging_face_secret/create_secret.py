import typer
from diffusion_util import util


def main():
    token = typer.prompt("hugging face api token:")
    config = util.get_config()
    secret_name = util.get_huggingface_secret_name(config)
    description = "Huggingfce api token for model hub"
    util.create_secret(secret_name, token, description=description)


if __name__ == "__main__":
    typer.run(main)