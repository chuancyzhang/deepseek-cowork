import {HostProvider} from './host/Host';
import WorkbenchMount from './plugins/WorkbenchMount';
import HostRecovery from './host/Recovery';
export default function App() {
  return <div className="window-shell">{/Windows/i.test(navigator.userAgent) && <div className="window-titlebar" aria-hidden="true"/>}<div className="window-content"><HostProvider><WorkbenchMount/><HostRecovery/></HostProvider></div></div>;
}
