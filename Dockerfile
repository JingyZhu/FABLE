FROM continuumio/anaconda3

# ? RUN useradd --system --create-home -d /home/fable --shell /bin/bash -G root -u 1001 fable

# ? USER fable
# ? WORKDIR /home/fable

# RUN mkdir /home/fable
RUN mkdir -p /home/fable/deps
COPY . /home/fable

# ? USER root
WORKDIR /home/fable
ENV PYTHONPATH=${PYTHONPATH}:/home/fable
# Prepare
RUN mkdir /usr/share/man/man1
RUN conda config --set changeps1 false 
# Install Java and other basic tools
RUN apt update && apt install -y wget \
    curl \
    openjdk-11-jdk \
    gcc g++ \
    net-tools sudo procps


RUN curl -sL https://deb.nodesource.com/setup_12.x | bash -
RUN apt install -y nodejs

# Install npm packages
RUN npm install chrome-remote-interface chrome-launcher yargs
RUN npm install -g http-server

# Install Chrome
RUN wget -q https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
RUN apt install -y ./google-chrome-stable_current_amd64.deb && rm google-chrome-stable_current_amd64.deb

# Install python dependencies
RUN pip install -r requirements.txt
# Install boilerpipe
RUN git clone https://github.com/misja/python-boilerpipe.git deps/python-boilerpipe
RUN pip install -e deps/python-boilerpipe

# Install vim 
RUN apt install -y vim

# Install tmux
RUN apt install -y tmux

# Install azure cli 
RUN curl -sL https://aka.ms/InstallAzureCLIDeb | bash

# ? USER fable

ENTRYPOINT python3
# WORKDIR /home 
# ENTRYPOINT /bin/sh -c /bin/bash


# # To run: sudo docker run --rm -it --mount type=bind,src=/mnt/fable-files,target=/mnt/fable-files --name fable fable 
# # Copy config.yml: sudo docker cp config.yml CONTAINER:/home/fable/fable/
# ENTRYPOINT ["python3", "rw.py"]


# 'FABLE_CONFIG_KEYVAULT'=1 'FABLE_CONFIG_VAULTNAME'='fabletestdockerkeyvault' 'FABLE_CONFIG_SECRETNAME'='fable-config' 'ROOT_USER'=1