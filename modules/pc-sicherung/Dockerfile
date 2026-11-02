# PC-Sicherung: Dienst, Netzboot und das Startsystem, das auf den PCs läuft.
# Ubuntu, weil dessen Bootlader und Kernel für Secure Boot signiert sind.
ARG UBUNTU=ubuntu:24.04
ARG RESTIC_VERSION=0.18.1
ARG RESTIC_SHA256=680838f19d67151adba227e1570cdd8af12c19cf1735783ed1ba928bc41f363d
ARG REST_SERVER_VERSION=0.14.0
ARG REST_SERVER_SHA256=4c9c95bc079a0334e81fad379b19dc5c3353c71c2c88d652cafce2081c2b1c66

FROM ${UBUNTU} AS tools
ARG RESTIC_VERSION RESTIC_SHA256 REST_SERVER_VERSION REST_SERVER_SHA256
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends ca-certificates curl bzip2 >/dev/null
RUN curl -fsSL -o /tmp/restic.bz2 \
      "https://github.com/restic/restic/releases/download/v${RESTIC_VERSION}/restic_${RESTIC_VERSION}_linux_amd64.bz2" \
    && echo "${RESTIC_SHA256}  /tmp/restic.bz2" | sha256sum -c - \
    && bunzip2 -c /tmp/restic.bz2 >/usr/local/bin/restic && chmod 755 /usr/local/bin/restic \
    && curl -fsSL -o /tmp/rs.tgz \
      "https://github.com/restic/rest-server/releases/download/v${REST_SERVER_VERSION}/rest-server_${REST_SERVER_VERSION}_linux_amd64.tar.gz" \
    && echo "${REST_SERVER_SHA256}  /tmp/rs.tgz" | sha256sum -c - \
    && tar -xzf /tmp/rs.tgz -C /tmp && install -m 755 /tmp/rest-server_*/rest-server /usr/local/bin/rest-server

# --- The system the PCs boot into ----------------------------------------------------------------
FROM ${UBUNTU} AS live
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends \
      linux-image-generic udev kmod busybox-static bash coreutils util-linux fdisk gdisk partclone \
      efibootmgr curl jq ca-certificates iproute2 procps tar zstd dosfstools >/dev/null
COPY --from=tools /usr/local/bin/restic /usr/local/bin/restic
COPY live/init /init
COPY live/hn-agent live/hn-udhcpc /usr/local/bin/
COPY live/trim.sh /tmp/trim.sh
RUN bash /tmp/trim.sh && rm /tmp/trim.sh

FROM ${UBUNTU} AS pack
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends cpio zstd \
      shim-signed grub-efi-amd64-signed ipxe >/dev/null
COPY --from=live / /rootfs/
RUN mkdir -p /out/live /out/tftp/grub /out/stick \
    && mv /rootfs/kernel /out/live/vmlinuz \
    && rm -rf /rootfs/proc/* /rootfs/sys/* /rootfs/dev/* \
    && mknod -m 600 /rootfs/dev/console c 5 1 && mknod -m 666 /rootfs/dev/null c 1 3 \
    && cd /rootfs && find . -xdev | sort | cpio -o -H newc --quiet | zstd -q -19 -T0 >/out/live/initrd.img \
    && cp /usr/lib/shim/shimx64.efi.signed.latest /out/tftp/shimx64.efi \
    && cp /usr/lib/grub/x86_64-efi-signed/grubnetx64.efi.signed /out/tftp/grubx64.efi \
    && cp /usr/lib/ipxe/undionly.kpxe /out/tftp/ipxe.0 \
    && cp /usr/lib/shim/shimx64.efi.signed.latest /out/stick/BOOTX64.EFI \
    && cp /usr/lib/shim/mmx64.efi /out/stick/mmx64.efi \
    && cp /usr/lib/grub/x86_64-efi-signed/grubx64.efi.signed /out/stick/grubx64.efi

# --- The service on the server -------------------------------------------------------------------
FROM ${UBUNTU}
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends \
      python3 dnsmasq-base apache2-utils mtools fdisk ca-certificates iproute2 tzdata >/dev/null \
    && rm -rf /var/lib/apt/lists/*
COPY --from=tools /usr/local/bin/restic /usr/local/bin/rest-server /usr/local/bin/
COPY --from=pack /out /srv
COPY service/ /app/
ENV PYTHONUNBUFFERED=1
CMD ["python3", "/app/server.py"]
