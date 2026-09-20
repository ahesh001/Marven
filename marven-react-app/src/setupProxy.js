const { createProxyMiddleware } = require('http-proxy-middleware');
module.exports = function(app) {
  const target = process.env.REACT_APP_API_BASE || 'http://localhost:8000';
  app.use('/api', createProxyMiddleware({ target, changeOrigin: true, ws: true, logLevel: 'silent' }));
  app.use('/api/**', createProxyMiddleware({ target, changeOrigin: true, ws: true, logLevel: 'silent' }));
};

