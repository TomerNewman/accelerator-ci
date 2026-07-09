FROM quay.io/openshift/origin-cli:4.20 AS oc-cli
FROM registry.access.redhat.com/ubi9/python-311:latest

LABEL org.opencontainers.image.authors="Red Hat Ecosystem Engineering"

USER root

COPY --from=oc-cli /usr/bin/oc /usr/bin/oc

ARG KCLI_VERSION=99.0.202512181223

COPY centos-stream.repo /etc/yum.repos.d/centos-stream.repo
RUN dnf swap -y openssl-fips-provider-so openssl-fips-provider --allowerasing && \
    dnf install -y --allowerasing \
        openssh-clients make jq genisoimage \
        libvirt-devel gcc python3.11-devel pkgconf-pkg-config && \
    dnf clean all

RUN pip install --no-cache-dir kcli==${KCLI_VERSION} libvirt-python

WORKDIR /opt/accelerator-ci

ARG ARTIFACT_DIR=/opt/accelerator-ci/test-results
ENV ARTIFACT_DIR="${ARTIFACT_DIR}"

COPY . .

RUN pip install --no-cache-dir . && \
    mkdir -p "${ARTIFACT_DIR}" && \
    chmod -R 777 /opt/accelerator-ci

ENTRYPOINT ["bash"]
