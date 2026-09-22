const camera = new URL(window.location.href);
camera.protocol = 'https:';
camera.port = '8443';
camera.pathname = '/';
camera.search = '';
camera.hash = '';
document.getElementById('open-camera').href = camera.href;
