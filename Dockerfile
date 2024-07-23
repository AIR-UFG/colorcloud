FROM pytorch/pytorch:2.3.0-cuda12.1-cudnn8-runtime

# best practice suggested in docs: https://docs.docker.com/engine/reference/builder/#env
ARG DEBIAN_FRONTEND=noninteractive

# best practice suggested in docs: https://docs.docker.com/develop/develop-images/instructions/#run
RUN apt update && apt install -y \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/* 

ARG git_user_name
ARG git_user_email
RUN git config --global user.name "$git_user_name" && \
    git config --global user.email $git_user_email 

# install quarto and its jupyterlab extension: https://quarto.org/docs/get-started/hello/jupyter.html
RUN curl -LO https://www.quarto.org/download/latest/quarto-linux-amd64.deb && \
    dpkg -i quarto-linux-amd64.deb && \
    rm quarto-linux-amd64.deb

RUN pip install \
    jupyterlab \
    nbdev \
    lightning \
    matplotlib \
    jupyterlab-quarto \
    jupyterlab-git \
    jupyterlab-vim


WORKDIR /workspace/colorcloud
COPY . /workspace/colorcloud
RUN nbdev_install_hooks && pip install -e '.[dev]'

RUN ln -sf /bin/bash /bin/sh

WORKDIR /workspace
CMD  ["jupyter", "lab", "--port=8888", "--no-browser", "--ip=0.0.0.0", "--allow-root", "--ContentsManager.allow_hidden=True"]
