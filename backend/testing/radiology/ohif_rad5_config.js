// RAD5 synthetic-only OHIF configuration. This is NOT a hospital deployment.
// Same-origin DICOMweb; no OAuth tokens, API credentials, or external data sources.
window.config = {
  routerBasename: '/',
  showStudyList: false,
  dataSources: [
    {
      namespace: '@ohif/extension-default.dataSourcesModule.dicomweb',
      sourceName: 'dicomweb',
      configuration: {
        friendlyName: 'CSP RAD5 synthetic local scope',
        name: 'RAD5-SYNTHETIC',
        qidoRoot: '/dicom-web',
        wadoRoot: '/dicom-web',
        wadoUriRoot: '/dicom-web',
        qidoSupportsIncludeField: true,
        supportsFuzzyMatching: false,
        supportsWildcard: false,
        supportsReject: false,
        dicomUploadEnabled: false,
        imageRendering: 'wadors',
        thumbnailRendering: 'wadors',
        enableStudyLazyLoad: true,
      },
    },
  ],
  defaultDataSourceName: 'dicomweb',
};
