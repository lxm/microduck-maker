#!/bin/bash
# Run in a private Linux mount namespace, on the builder's disposable ext4 copy.
set -euo pipefail
fs=$(realpath "$1")
inputs=$(realpath "$2")
kernel=$3
here=$(cd "$(dirname "$0")" && pwd)
test -f "$fs" && test ! -b "$fs"
root=$(mktemp -d)
mount -o loop "$fs" "$root"
cleanup() {
    umount -R "$root"
    rmdir "$root"
}
trap cleanup EXIT
mount -t proc proc "$root/proc"
mount -t tmpfs tmpfs "$root/dev"
for spec in 'null 3' 'zero 5' 'random 8' 'urandom 9'; do
    read -r name minor <<< "$spec"
    mknod -m 666 "$root/dev/$name" c 1 "$minor"
done
mount -t tmpfs tmpfs "$root/tmp"
mkdir "$root/tmp/camera"
cp "$inputs/loop.deb" "$here/vin-uyvy.c" "$root/tmp/camera/"
# Some minimal images use a resolvconf symlink whose target only exists at boot.
mv "$root/etc/resolv.conf" "$root/tmp/camera/resolv.conf"
cp -L /etc/resolv.conf "$root/etc/resolv.conf"
if [[ -e "$root/usr/sbin/policy-rc.d" || -L "$root/usr/sbin/policy-rc.d" ]]; then
    mv "$root/usr/sbin/policy-rc.d" "$root/tmp/camera/policy-rc.d"
fi
printf '#!/bin/sh\nexit 101\n' > "$root/usr/sbin/policy-rc.d"
chmod +x "$root/usr/sbin/policy-rc.d"
install -Dm644 "$inputs/libgstrswebrtc.so" "$root/usr/local/lib/gstreamer-1.0/libgstrswebrtc.so"
install -Dm644 "$inputs/libgstrsrtp.so" "$root/usr/local/lib/gstreamer-1.0/libgstrsrtp.so"
chroot "$root" /bin/bash -se -- "$kernel" <<'CHROOT'
set -euo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin DEBIAN_FRONTEND=noninteractive LC_ALL=C
cd /tmp/camera
kernel=$1
printf '%s\n' 'deb https://deb.debian.org/debian trixie main' 'deb https://deb.debian.org/debian-security trixie-security main' > debian.list
apt=(apt-get -o Dir::Etc::sourcelist=/tmp/camera/debian.list -o Dir::Etc::sourceparts=- -o Acquire::Retries=3)
"${apt[@]}" update --error-on=any
"${apt[@]}" install -y --no-install-recommends v4l-utils gstreamer1.0-tools gstreamer1.0-plugins-{base,good,bad,ugly} gstreamer1.0-nice
# Remove only packages added for compilation. Keep the original kernel headers.
dpkg-query -W -f='${db:Status-Status} ${binary:Package}\n' | sed -n 's/^installed //p' | sort > before
"${apt[@]}" install -y --no-install-recommends gcc make libc6-dev linux-libc-dev
dpkg-query -W -f='${db:Status-Status} ${binary:Package}\n' | sed -n 's/^installed //p' | sort > after
dpkg-deb -x loop.deb source
src=$PWD/source/usr/src/v4l2loopback-0.15.0
make -C "/usr/src/linux-headers-$kernel" M="$src" ARCH=arm64 -j2 modules
test "$(modinfo -F vermagic "$src/v4l2loopback.ko" | awk '{print $1}')" = "$kernel"
install -Dm644 "$src/v4l2loopback.ko" "/usr/lib/modules/$kernel/updates/microduck-vision/v4l2loopback.ko"
cc -Wall -Werror -O2 vin-uyvy.c -o vin-uyvy
install -Dm755 vin-uyvy /usr/local/libexec/microduck-vin-uyvy
comm -13 before after | xargs -r "${apt[@]}" purge -y
export GST_PLUGIN_PATH=/usr/local/lib/gstreamer-1.0 GST_REGISTRY=/tmp/camera/gst-registry.bin
for element in v4l2src x264enc webrtcbin webrtcsink nicesrc; do gst-inspect-1.0 "$element" >/dev/null; done
apt-get clean
rm -f /etc/resolv.conf /usr/sbin/policy-rc.d
mv resolv.conf /etc/resolv.conf
if [[ -e policy-rc.d || -L policy-rc.d ]]; then mv policy-rc.d /usr/sbin/policy-rc.d; fi
CHROOT
